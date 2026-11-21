import os
import re
import base64
from io import BytesIO
from urllib.parse import quote
import json
import html
import uuid
import asyncio
import threading
import textwrap
import requests
import streamlit as st
import streamlit.components.v1 as components

from crewai import Agent, Task, Crew, Process
from crewai.llm import LLM
from crewai.tools import tool
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# =====================================================
# CONFIGURATION DES SECRETS
# =====================================================
def secret(nom, defaut=""):
    try:
        return str(st.secrets.get(nom, defaut))
    except Exception:
        return defaut


os.environ["GROQ_API_KEY"] = secret("GROQ_API_KEY")
os.environ["GEMINI_API_KEY"] = secret("GEMINI_API_KEY")
os.environ["OPENROUTER_API_KEY"] = secret("OPENROUTER_API_KEY")
TELEGRAM_TOKEN = secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = secret("TELEGRAM_CHAT_ID").strip()

MODELES = {
    "groq": secret("MODEL_GROQ", "groq/openai/gpt-oss-120b"),
    "gemini": secret("MODEL_GEMINI", "gemini/gemini-3.8-flash"),
    "openrouter": secret("MODEL_OPENROUTER", "openrouter/openrouter/free"),
}

DOSSIER_VIDEOS = "/tmp/agence_videos"
os.makedirs(DOSSIER_VIDEOS, exist_ok=True)
DOSSIER_FICHIERS = "/tmp/agence_fichiers"
os.makedirs(DOSSIER_FICHIERS, exist_ok=True)
DOSSIER_IMAGES = "/tmp/agence_images"
os.makedirs(DOSSIER_IMAGES, exist_ok=True)


# =====================================================
# MODÈLES
# =====================================================
def get_llm(fournisseur="groq", temp=0.2):
    cles = {
        "groq": os.environ.get("GROQ_API_KEY"),
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "openrouter": os.environ.get("OPENROUTER_API_KEY"),
    }
    return LLM(model=MODELES[fournisseur], temperature=temp, api_key=cles[fournisseur])


# =====================================================
# TELEGRAM : ENVOI DE MESSAGES ET DE VIDÉOS
# =====================================================
def send_telegram(msg):
    """Retourne (succès, détail)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant dans les secrets."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        for i in range(0, max(len(msg), 1), 4000):
            r = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[i : i + 4000]},
                timeout=15,
            )
            if not r.ok:
                return False, r.text
        return True, "OK"
    except Exception as e:
        return False, str(e)


def send_telegram_video(chemin, legende=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram non configuré."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"
    try:
        with open(chemin, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": legende[:1000]},
                files={"video": f},
                timeout=120,
            )
        return (r.ok, "OK" if r.ok else r.text)
    except Exception as e:
        return False, str(e)


# =====================================================
# CRÉATION DE VIDÉO VERTICALE (9:16) AVEC MOVIEPY
# =====================================================
def _police(taille):
    from PIL import ImageFont

    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            return ImageFont.truetype(p, taille)
    return ImageFont.load_default(size=taille)


def fabriquer_video(texte, nom="video", duree_scene=3, voix=False):
    """Une scène par ligne de texte. Retourne le chemin du fichier mp4."""
    import numpy as np
    from PIL import Image, ImageDraw
    from moviepy import ImageClip, concatenate_videoclips, vfx, AudioFileClip, CompositeAudioClip

    scenes = [l.strip() for l in texte.split("\n") if l.strip()][:12]
    if not scenes:
        raise ValueError("Texte vide.")

    couleurs = [(20, 20, 40), (40, 20, 60), (10, 60, 70), (70, 30, 30)]
    largeur, hauteur = 1080, 1920
    police = _police(80)
    clips, audios, t = [], [], 0.0

    for i, scene in enumerate(scenes):
        img = Image.new("RGB", (largeur, hauteur), couleurs[i % len(couleurs)])
        dessin = ImageDraw.Draw(img)
        bloc = "\n".join(textwrap.wrap(scene, width=18))
        x0, y0, x1, y1 = dessin.multiline_textbbox(
            (0, 0), bloc, font=police, align="center", spacing=20
        )
        pos = ((largeur - (x1 - x0)) / 2 - x0, (hauteur - (y1 - y0)) / 2 - y0)
        dessin.multiline_text(
            pos, bloc, font=police, fill=(255, 255, 255), align="center", spacing=20
        )
        duree = duree_scene
        if voix:
            try:
                chemin_a = os.path.join(DOSSIER_VIDEOS, f"voix_{uuid.uuid4().hex[:6]}.mp3")
                synthese_vocale(scene, chemin_a)
                a = AudioFileClip(chemin_a)
                duree = max(duree_scene, a.duration + 0.6)
                audios.append(a.with_start(t + 0.2))
            except Exception as e:
                print(f"Voix indisponible, vidéo sans voix : {e}")
                voix = False
        t += duree
        clip = ImageClip(np.array(img)).with_duration(duree)
        try:
            clip = clip.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(0.4)])
        except Exception:
            pass
        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")
    nom_propre = "".join(c for c in nom if c.isalnum() or c in "-_") or "video"
    chemin = os.path.join(DOSSIER_VIDEOS, f"{nom_propre}_{uuid.uuid4().hex[:6]}.mp4")
    if audios:
        final = final.with_audio(CompositeAudioClip(audios))
    final.write_videofile(
        chemin, fps=24, codec="libx264", audio=bool(audios), audio_codec="aac", logger=None
    )
    for a in audios:
        a.close()
    return chemin


# =====================================================
# FICHIERS PRODUITS : LIVRAISON, EBOOK 3D (FLIPBOOK), LOGICIELS
# =====================================================
def send_telegram_document(chemin, legende=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram non configuré."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        with open(chemin, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": legende[:1000]},
                files={"document": f},
                timeout=120,
            )
        return (r.ok, "OK" if r.ok else r.text)
    except Exception as e:
        return False, str(e)


def livrer(chemin, legende=""):
    ok, detail = send_telegram_document(chemin, legende)
    return "envoyé sur Telegram" if ok else f"non envoyé sur Telegram ({detail})"


def _nom_fichier(nom, defaut="fichier"):
    propre = "".join(c for c in nom if c.isalnum() or c in "-_") or defaut
    return f"{propre}_{uuid.uuid4().hex[:5]}"


# --- FLIPBOOK START ---
def _decouper(bloc, maxi):
    """Coupe un long paragraphe en morceaux (par phrases)."""
    if len(bloc) <= maxi:
        return [bloc]
    phrases = re.split(r"(?<=[.!?…])\s+", bloc)
    morceaux, cour = [], ""
    for ph in phrases:
        if cour and len(cour) + len(ph) + 1 > maxi:
            morceaux.append(cour)
            cour = ph
        else:
            cour = (cour + " " + ph).strip()
    if cour:
        morceaux.append(cour)
    return morceaux


_CACHE_IMG = {}


def _charger_image(source):
    """Retourne les octets d'une image. Sources : URL http(s), 'ia:description', 'fichier:nom.jpg'."""
    source = source.strip()
    if source in _CACHE_IMG:
        return _CACHE_IMG[source]
    if source.lower().startswith("fichier:"):
        chemin = os.path.join(DOSSIER_IMAGES, os.path.basename(source[8:].strip()))
        with open(chemin, "rb") as f:
            return f.read()
    if source.lower().startswith("ia:"):
        url = (
            "https://image.pollinations.ai/prompt/"
            + quote(source[3:].strip())
            + "?width=768&height=768&nologo=true"
        )
        timeout = 120
    elif source.lower().startswith(("http://", "https://")):
        url, timeout = source, 30
    else:
        raise ValueError("Source d'image inconnue (http..., ia:..., fichier:...).")
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    if len(r.content) > 15_000_000:
        raise ValueError("Image trop lourde (15 Mo max).")
    _CACHE_IMG[source] = r.content
    return r.content


def _image_html(legende, source):
    """Réduit l'image et l'intègre dans le fichier (base64) : le livre reste autonome."""
    from PIL import Image

    img = Image.open(BytesIO(_charger_image(source))).convert("RGB")
    img.thumbnail((900, 900))
    tampon = BytesIO()
    img.save(tampon, format="JPEG", quality=78, optimize=True)
    b64 = base64.b64encode(tampon.getvalue()).decode("ascii")
    cap = f"<figcaption>{html.escape(legende)}</figcaption>" if legende.strip() else ""
    return (
        f'<figure class="fig"><img alt="{html.escape(legende)}" '
        f'src="data:image/jpeg;base64,{b64}">{cap}</figure>'
    )


MOTIF_IMAGE = re.compile(r"^!\[(.*?)\]\((.+)\)$")


def _paginer(texte, budget=620, avertissements=None):
    """'# Titre' = chapitre (nouvelle page), '## Titre' = sous-titre,
    '![légende](source)' = image (source : URL, ia:description ou fichier:nom.jpg)."""
    avertissements = avertissements if avertissements is not None else []
    pages, cour, taille = [], [], 0

    def vider():
        nonlocal cour, taille
        if cour:
            pages.append("".join(cour))
            cour, taille = [], 0

    for bloc in [b.strip() for b in texte.split("\n") if b.strip()]:
        img_m = MOTIF_IMAGE.match(bloc)
        if img_m:
            try:
                fig = _image_html(img_m.group(1), img_m.group(2))
            except Exception as e:
                avertissements.append(f"Image ignorée ({img_m.group(2)[:60]}) : {e}")
                continue
            if taille + 330 > budget:
                vider()
            cour.append(fig)
            taille += 330
        elif bloc.startswith("## "):
            if taille + 120 > budget:
                vider()
            cour.append(f"<h3>{html.escape(bloc[3:])}</h3>")
            taille += 120
        elif bloc.startswith("# "):
            vider()
            cour.append(f'<h2 class="chap">{html.escape(bloc[2:])}</h2>')
            taille += 230
        else:
            for m in _decouper(bloc, budget - 80):
                if taille + len(m) > budget:
                    vider()
                cour.append(f"<p>{html.escape(m)}</p>")
                taille += len(m) + 40
    vider()
    return pages or ["<p></p>"]


MODELE_FLIPBOOK = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html,body{height:100%;overflow:hidden;background:radial-gradient(ellipse at center,#4a3a2a 0%,#1c140d 100%);font-family:Georgia,'Times New Roman',serif;color:#eee;user-select:none;-webkit-user-select:none}
#wrap{position:absolute;top:0;left:0;right:0;bottom:64px;display:flex;align-items:center;justify-content:center;touch-action:none;cursor:pointer}
#book{position:relative;width:calc(var(--pw)*2);height:var(--ph);perspective:2600px;transition:transform .9s cubic-bezier(.6,.05,.3,1)}
#book.single{width:var(--pw)}
.sheet{position:absolute;top:0;left:50%;width:var(--pw);height:var(--ph);transform-origin:left center;transform-style:preserve-3d;transition:transform .9s cubic-bezier(.6,.05,.3,1)}
#book.single .sheet{left:0}
.sheet.gone{visibility:hidden}
.face{position:absolute;top:0;left:0;right:0;bottom:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;overflow:hidden}
.face.back{transform:rotateY(180deg)}
.paper{background:radial-gradient(rgba(120,90,40,.05) 1px,transparent 1.2px) 0 0/4px 4px,linear-gradient(#fbf7ec,#f3ecd9)}
.endpaper{background:#e8dcc0}
.front.paper{border-radius:2px 6px 6px 2px;box-shadow:inset -3px 0 6px rgba(0,0,0,.08)}
.back.paper{border-radius:6px 2px 2px 6px;box-shadow:inset 3px 0 6px rgba(0,0,0,.08)}
.front.paper::before,.back.paper::before{content:"";position:absolute;top:0;bottom:0;width:14%;pointer-events:none}
.front.paper::before{left:0;background:linear-gradient(to right,rgba(70,45,10,.30),rgba(70,45,10,0))}
.back.paper::before{right:0;background:linear-gradient(to left,rgba(70,45,10,.30),rgba(70,45,10,0))}
.cover{background:radial-gradient(rgba(0,0,0,.16) 1px,transparent 1.5px) 0 0/5px 5px,linear-gradient(135deg,rgba(255,255,255,.14),rgba(0,0,0,.42)),var(--cov);border-radius:3px 9px 9px 3px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08),inset 6px 0 10px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;text-align:center;padding:12%}
.cover.back{border-radius:9px 3px 3px 9px;box-shadow:inset -6px 0 10px rgba(0,0,0,.35)}
.cover .ct{border:2px solid rgba(217,180,90,.7);outline:1px solid rgba(217,180,90,.35);outline-offset:6px;padding:12% 8%;width:100%}
.cover h1{font-size:calc(var(--fs)*1.9);color:#e6c36a;text-shadow:0 1px 0 rgba(0,0,0,.6),0 0 8px rgba(230,195,106,.25);line-height:1.2;letter-spacing:.04em;font-weight:700}
.cover .sep{width:40%;height:2px;background:#d9b45a;margin:1.2em auto;opacity:.8}
.cover p{font-size:calc(var(--fs)*1.05);color:#e6d3a0;letter-spacing:.12em;text-transform:uppercase}
.shade{position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;opacity:0;background:linear-gradient(to right,rgba(0,0,0,.55),rgba(0,0,0,0) 70%)}
.back .shade{background:linear-gradient(to left,rgba(0,0,0,.55),rgba(0,0,0,0) 70%)}
.sheet.flipping .shade{animation:sh .9s ease-in-out}
@keyframes sh{0%{opacity:0}50%{opacity:.6}100%{opacity:0}}
.inner{position:relative;height:100%;padding:10% 10% 14% 11%;font-size:var(--fs);line-height:1.55;color:#2b2217;text-align:justify;hyphens:auto;-webkit-hyphens:auto;overflow:hidden}
.back .inner{padding:10% 11% 14% 10%}
.inner p{margin-bottom:.65em;text-indent:1.3em}
.inner h2.chap{font-size:calc(var(--fs)*1.7);text-align:center;margin:.4em 0 1em;font-weight:700;color:#4a2c1a;line-height:1.2}
.inner h2.chap::after{content:"";display:block;width:30%;height:1px;background:#8a6a3a;margin:.6em auto 0}
.inner h3{font-size:calc(var(--fs)*1.2);margin:.8em 0 .5em;color:#4a2c1a}
.inner h2.chap + p{text-indent:0}
.inner h2.chap + p::first-letter{float:left;font-size:calc(var(--fs)*3.2);line-height:.85;padding:.05em .1em 0 0;color:#7a3b1d;font-weight:700}
.fig{margin:.5em 0 .8em;text-align:center}
.fig img{max-width:100%;max-height:calc(var(--ph)*.4);object-fit:contain;border-radius:2px;box-shadow:0 2px 6px rgba(60,40,10,.35);filter:sepia(.08)}
.fig figcaption{font-size:.82em;font-style:italic;color:#7a6a4a;margin-top:.4em}
.pn{position:absolute;bottom:5%;left:0;right:0;text-align:center;font-size:calc(var(--fs)*.8);color:#8a7550}
.edge{position:absolute;top:2px;bottom:2px;width:0;background:repeating-linear-gradient(90deg,#f6f0e0 0,#f6f0e0 1px,#cbbf9f 1px,#cbbf9f 2px);box-shadow:0 1px 3px rgba(0,0,0,.4);z-index:0}
.floor{position:absolute;left:5%;right:5%;bottom:-18px;height:24px;background:radial-gradient(ellipse at center,rgba(0,0,0,.55),transparent 70%);filter:blur(6px);z-index:0}
#bar{position:fixed;left:0;right:0;bottom:0;height:64px;display:flex;align-items:center;justify-content:center;gap:14px;padding-bottom:env(safe-area-inset-bottom)}
#bar button{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:#f0e4c8;border-radius:999px;padding:8px 16px;font-size:15px;cursor:pointer;font-family:inherit}
#bar button:disabled{opacity:.35;cursor:default}
#cnt{min-width:130px;text-align:center;font-size:14px;color:#d8c9a6}
</style></head>
<body>
<div id="wrap"><div id="book"></div></div>
<div id="bar"><button id="pv">&#8592;</button><span id="cnt"></span><button id="nx">&#8594;</button><button id="fs">&#9974;</button></div>
<script>
(function(){
var D = __DATA__;
var root = document.documentElement;
var book = document.getElementById('book');
var wrap = document.getElementById('wrap');
var cnt = document.getElementById('cnt');
var bPv = document.getElementById('pv');
var bNx = document.getElementById('nx');
var sheets = [], cur = 0, mode = '', er = null, el = null, swiped = false;
var N = D.pages.length;
root.style.setProperty('--cov', D.color);

function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function getMode(){ return (window.innerWidth >= 820 && window.innerWidth >= window.innerHeight*1.05) ? 'double' : 'single'; }
function size(){
  var aw = window.innerWidth, ah = window.innerHeight - 70;
  var across = mode === 'double' ? 2 : 1;
  var ph = Math.min(ah*0.92, (aw*0.94/across)*1.5);
  var pw = ph/1.5;
  root.style.setProperty('--pw', pw+'px');
  root.style.setProperty('--ph', ph+'px');
  root.style.setProperty('--fs', (pw/24)+'px');
}
function coverFront(){ return '<div class="ct"><h1>'+esc(D.title)+'</h1><div class="sep"></div><p>'+esc(D.author)+'</p></div>'; }
function coverBack(){ return '<div class="ct"><div class="sep" style="margin-top:0"></div><p>'+esc(D.title)+'</p></div>'; }
function pageHTML(i){ return '<div class="inner">'+D.pages[i]+'</div><div class="pn">'+(i+1)+'</div>'; }
function maxCur(){ return mode === 'single' ? sheets.length - 1 : sheets.length; }

function pageNow(){
  if (!sheets.length) return 0;
  if (mode === 'double') {
    if (cur === 0) return 0;
    if (cur === sheets.length) return N + 1;
    return 2*cur - 1;
  }
  return cur;
}

function build(pn){
  mode = getMode(); size();
  book.className = mode === 'single' ? 'single' : '';
  book.innerHTML = '<div class="floor"></div><div class="edge" id="er" style="left:100%"></div><div class="edge" id="el" style="right:100%"></div>';
  er = book.querySelector('#er'); el = book.querySelector('#el');
  sheets = [];
  var defs = [], i;
  var blank = {c:'paper', h:''};
  if (mode === 'double') {
    var f = [{c:'cover', h:coverFront()}, {c:'paper endpaper', h:''}];
    for (i = 0; i < N; i++) f.push({c:'paper', h:pageHTML(i)});
    if (f.length % 2 === 0) f.push(blank);
    f.push({c:'cover', h:coverBack()});
    for (i = 0; i < f.length; i += 2) defs.push([f[i], f[i+1]]);
  } else {
    defs.push([{c:'cover', h:coverFront()}, {c:'paper endpaper', h:''}]);
    for (i = 0; i < N; i++) defs.push([{c:'paper', h:pageHTML(i)}, blank]);
    defs.push([{c:'cover', h:coverBack()}, blank]);
  }
  defs.forEach(function(d){
    var s = document.createElement('div'); s.className = 'sheet';
    s.innerHTML = '<div class="face front '+d[0].c+'">'+d[0].h+'<div class="shade"></div></div>' +
                  '<div class="face back '+d[1].c+'">'+d[1].h+'<div class="shade"></div></div>';
    book.appendChild(s); sheets.push(s);
  });
  if (mode === 'single') cur = Math.max(0, Math.min(pn, sheets.length - 1));
  else cur = pn === 0 ? 0 : (pn > N ? sheets.length : Math.min(sheets.length, Math.floor(pn/2) + 1));
  apply(false); ui();
}

function apply(animate){
  sheets.forEach(function(s, i){
    var fl = i < cur;
    var zFinal = fl ? i + 1 : sheets.length - i;
    if (s._fl === fl) { s.style.zIndex = zFinal; return; }
    s._fl = fl;
    s.style.transition = animate ? '' : 'none';
    s.classList.remove('gone');
    s.style.transform = fl ? 'rotateY(-180deg)' : 'rotateY(0deg)';
    if (animate) {
      s.style.zIndex = 1000;
      s.classList.add('flipping');
      clearTimeout(s._t);
      s._t = setTimeout(function(){
        s.style.zIndex = zFinal; s.classList.remove('flipping');
        if (fl && mode === 'single') s.classList.add('gone');
      }, 900);
    } else {
      s.style.zIndex = zFinal;
      if (fl && mode === 'single') s.classList.add('gone');
    }
  });
  if (mode === 'double') {
    var shift = cur === 0 ? -25 : (cur === sheets.length ? 25 : 0);
    book.style.transform = 'translateX(' + shift + '%)';
    er.style.display = el.style.display = 'block';
    er.style.width = Math.min(14, (sheets.length - cur) * 0.9) + 'px';
    el.style.width = Math.min(14, cur * 0.9) + 'px';
  } else {
    book.style.transform = 'none';
    er.style.display = el.style.display = 'none';
  }
}

function label(){
  if (cur === 0) return 'Couverture';
  if (mode === 'double') {
    if (cur === sheets.length) return 'Fin';
    var l = 2*cur - 2, r = 2*cur - 1, parts = [];
    if (l >= 1 && l <= N) parts.push(l);
    if (r >= 1 && r <= N) parts.push(r);
    return 'Page ' + parts.join('\u2013') + ' / ' + N;
  }
  if (cur > N) return 'Fin';
  return 'Page ' + cur + ' / ' + N;
}
function ui(){ cnt.textContent = label(); bPv.disabled = cur <= 0; bNx.disabled = cur >= maxCur(); }
function next(){ if (cur < maxCur()) { cur++; apply(true); ui(); } }
function prev(){ if (cur > 0) { cur--; apply(true); ui(); } }

wrap.addEventListener('click', function(e){
  if (swiped) return;
  var r = book.getBoundingClientRect();
  var mid = mode === 'double' ? r.left + r.width/2 : r.left + r.width*0.4;
  if (e.clientX > mid) next(); else prev();
});
var tx = null;
wrap.addEventListener('touchstart', function(e){ tx = e.changedTouches[0].clientX; }, {passive:true});
wrap.addEventListener('touchend', function(e){
  if (tx === null) return;
  var dx = e.changedTouches[0].clientX - tx; tx = null;
  if (Math.abs(dx) > 40) { if (dx < 0) next(); else prev(); swiped = true; setTimeout(function(){ swiped = false; }, 400); }
}, {passive:true});
document.addEventListener('keydown', function(e){
  if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') next();
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') prev();
});
bPv.addEventListener('click', function(e){ e.stopPropagation(); prev(); });
bNx.addEventListener('click', function(e){ e.stopPropagation(); next(); });
document.getElementById('fs').addEventListener('click', function(e){
  e.stopPropagation();
  try { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen(); } catch(err) {}
});
var rt = null;
window.addEventListener('resize', function(){ clearTimeout(rt); rt = setTimeout(function(){ build(pageNow()); }, 150); });
build(0);
})();
</script></body></html>
"""


def construire_flipbook_html(titre, auteur, texte, couleur="#5b2a1f", avertissements=None):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", couleur or ""):
        couleur = "#5b2a1f"
    donnees = {
        "title": titre,
        "author": auteur,
        "color": couleur,
        "pages": _paginer(texte, avertissements=avertissements),
    }
    js = json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/")
    return MODELE_FLIPBOOK.replace("__DATA__", js).replace("__TITLE__", html.escape(titre))
# --- FLIPBOOK END ---


def fabriquer_ebook_3d(titre, auteur, texte, couleur="#5b2a1f"):
    """Crée un livre 3D à feuilleter (fichier HTML unique, sans internet requis)."""
    if not texte.strip():
        raise ValueError("Texte vide.")
    chemin = os.path.join(DOSSIER_FICHIERS, f"ebook_{_nom_fichier(titre, 'livre')}.html")
    avertissements = []
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(construire_flipbook_html(titre, auteur, texte, couleur, avertissements))
    return chemin, avertissements


def sauver_logiciel(nom, code, extension="html"):
    ext = (extension or "html").lower().strip(".")
    if ext not in {"html", "py", "js", "css", "json", "md", "txt"}:
        raise ValueError("Extension non autorisée (html, py, js, css, json, md, txt).")
    chemin = os.path.join(DOSSIER_FICHIERS, f"{_nom_fichier(nom, 'logiciel')}.{ext}")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(code)
    return chemin


# =====================================================
# VOIX OFF (synthèse vocale) : edge-tts, puis gTTS en secours
# =====================================================
VOIX_DEFAUT = "fr-FR-DeniseNeural"  # voix d'homme possible : fr-FR-HenriNeural


def _nettoyer_pour_voix(texte):
    texte = re.sub(r"!\[.*?\]\(.*?\)", " ", texte)
    texte = re.sub(r"https?://\S+", " ", texte)
    texte = re.sub(r"[#*_`>|~]", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


def _executer_async(fabrique_coro):
    """Lance une coroutine dans un thread propre (évite les conflits de boucle asyncio)."""
    erreur = {}

    def cible():
        try:
            asyncio.run(fabrique_coro())
        except Exception as e:
            erreur["e"] = e

    t = threading.Thread(target=cible)
    t.start()
    t.join()
    if "e" in erreur:
        raise erreur["e"]


def synthese_vocale(texte, chemin):
    """Transforme un texte en fichier mp3. Retourne le chemin."""
    texte = _nettoyer_pour_voix(texte)
    if not texte:
        raise ValueError("Texte vide pour la voix.")
    voix = secret("VOIX_TTS", VOIX_DEFAUT)
    erreurs = []
    try:
        import edge_tts

        async def _go():
            await edge_tts.Communicate(texte, voix).save(chemin)

        _executer_async(_go)
        if os.path.exists(chemin) and os.path.getsize(chemin) > 0:
            return chemin
        erreurs.append("edge-tts : fichier vide")
    except Exception as e:
        erreurs.append(f"edge-tts : {e}")
    try:
        from gtts import gTTS

        gTTS(text=texte, lang="fr").save(chemin)
        return chemin
    except Exception as e:
        erreurs.append(f"gTTS : {e}")
    raise RuntimeError(" | ".join(erreurs))


def send_telegram_audio(chemin, legende=""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False, "Telegram non configuré."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAudio"
    try:
        with open(chemin, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": legende[:1000]},
                files={"audio": f},
                timeout=120,
            )
        return (r.ok, "OK" if r.ok else r.text)
    except Exception as e:
        return False, str(e)


# =====================================================
# VIDÉO DU LIVRE : le livre s'ouvre, les pages se tournent, une voix lit le texte
# =====================================================
# --- LIVREVIDEO START ---
LARG_V, HAUT_V = 720, 1280          # format vertical 9:16
PW, PH = 540, 860                   # taille d'une page dans la vidéo
X0, Y0 = (LARG_V - PW) // 2, (HAUT_V - PH) // 2 + 20


def _police_serif(taille, gras=False):
    from PIL import ImageFont

    nom = "DejaVuSerif-Bold.ttf" if gras else "DejaVuSerif.ttf"
    for dossier in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/"):
        if os.path.exists(dossier + nom):
            return ImageFont.truetype(dossier + nom, taille)
    return _police(taille)


def _lignes_px(texte, police, largeur):
    lignes, cour = [], ""
    for mot in texte.split():
        test = (cour + " " + mot).strip()
        if police.getlength(test) <= largeur:
            cour = test
        else:
            if cour:
                lignes.append(cour)
            cour = mot
    if cour:
        lignes.append(cour)
    return lignes


def _hex_rgb(couleur):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", couleur or ""):
        couleur = "#5b2a1f"
    return tuple(int(couleur[i : i + 2], 16) for i in (1, 3, 5))


def _pages_video(texte, avertissements):
    """Découpe le texte en pages en mesurant la place réelle. Retourne [{'cmds':[...], 'narr':str}]."""
    from PIL import Image

    mx, mh, mb = 52, 62, 90
    larg, haut = PW - 2 * mx, PH - mh - mb
    f_txt, f_h2, f_h3, f_cap = (
        _police_serif(25), _police_serif(38, True), _police_serif(30, True), _police_serif(21),
    )
    lh_txt, lh_h2, lh_h3, lh_cap = 38, 52, 42, 30
    pages, etat = [], {"cmds": [], "narr": [], "y": 0}

    def vider():
        if etat["cmds"]:
            pages.append({"cmds": etat["cmds"], "narr": " ".join(etat["narr"])})
        etat["cmds"], etat["narr"], etat["y"] = [], [], 0

    for bloc in [b.strip() for b in texte.split("\n") if b.strip()]:
        m_img = MOTIF_IMAGE.match(bloc)
        if m_img:
            try:
                img = Image.open(BytesIO(_charger_image(m_img.group(2)))).convert("RGB")
            except Exception as e:
                avertissements.append(f"Image ignorée ({m_img.group(2)[:60]}) : {e}")
                continue
            ratio = min(larg / img.width, 300 / img.height)
            img = img.resize((max(1, int(img.width * ratio)), max(1, int(img.height * ratio))))
            leg = _lignes_px(m_img.group(1), f_cap, larg) if m_img.group(1).strip() else []
            besoin = img.height + 12 + len(leg) * lh_cap + 14
            if etat["y"] + besoin > haut:
                vider()
            etat["cmds"].append(("i", etat["y"], img))
            etat["y"] += img.height + 12
            for l in leg:
                etat["cmds"].append(("t", etat["y"], l, f_cap, "c", (120, 100, 70)))
                etat["y"] += lh_cap
            etat["y"] += 14
        elif bloc.startswith("## "):
            lg = _lignes_px(bloc[3:], f_h3, larg)
            if etat["y"] + len(lg) * lh_h3 + 16 + 2 * lh_txt > haut:
                vider()
            for l in lg:
                etat["cmds"].append(("t", etat["y"], l, f_h3, "l", (74, 44, 26)))
                etat["y"] += lh_h3
            etat["y"] += 10
            etat["narr"].append(bloc[3:].strip() + ".")
        elif bloc.startswith("# "):
            vider()
            for l in _lignes_px(bloc[2:], f_h2, larg):
                etat["cmds"].append(("t", etat["y"], l, f_h2, "c", (74, 44, 26)))
                etat["y"] += lh_h2
            etat["y"] += 30
            etat["narr"].append(bloc[2:].strip() + ".")
        else:
            lignes_page = []
            for l in _lignes_px(bloc, f_txt, larg):
                if etat["y"] + lh_txt > haut:
                    if lignes_page:
                        etat["narr"].append(" ".join(lignes_page))
                        lignes_page = []
                    vider()
                etat["cmds"].append(("t", etat["y"], l, f_txt, "l", (43, 34, 23)))
                etat["y"] += lh_txt
                lignes_page.append(l)
            if lignes_page:
                etat["narr"].append(" ".join(lignes_page))
            etat["y"] += 14
    vider()
    return pages


def _rendre_page(cmds, numero, total):
    from PIL import Image, ImageDraw

    papier, brun = (251, 247, 236), (70, 45, 10)
    im = Image.new("RGB", (PW, PH), papier)
    d = ImageDraw.Draw(im)
    for x in range(46):  # ombre de la reliure (à gauche)
        k = 0.30 * (1 - x / 46)
        d.line([(x, 0), (x, PH)], fill=tuple(int(p * (1 - k) + b * k) for p, b in zip(papier, brun)))
    mx, mh = 52, 62
    larg = PW - 2 * mx
    for cmd in cmds:
        if cmd[0] == "t":
            _, y, texte, police, align, couleur = cmd
            x = mx if align == "l" else mx + (larg - police.getlength(texte)) / 2
            d.text((x, mh + y), texte, font=police, fill=couleur)
        else:
            _, y, img = cmd
            im.paste(img, (mx + (larg - img.width) // 2, mh + y))
    f_num = _police_serif(20)
    num = str(numero)
    d.text(((PW - f_num.getlength(num)) / 2, PH - 52), num, font=f_num, fill=(138, 117, 80))
    return im.convert("RGBA")


def _rendre_couverture(titre, auteur, couleur):
    from PIL import Image, ImageDraw

    base = _hex_rgb(couleur)
    im = Image.new("RGB", (PW, PH))
    d = ImageDraw.Draw(im)
    for y in range(PH):
        f = 1.15 - 0.55 * y / PH
        d.line([(0, y), (PW, y)], fill=tuple(max(0, min(255, int(c * f))) for c in base))
    for x in range(34):  # reliure
        a = 0.45 * (1 - x / 34)
        d.line([(x, 0), (x, PH)], fill=tuple(int(c * (1 - a)) for c in base))
    d.rectangle([40, 40, PW - 40, PH - 40], outline=(217, 180, 90), width=3)
    d.rectangle([52, 52, PW - 52, PH - 52], outline=(160, 130, 60), width=1)
    f_t, f_a = _police_serif(50, True), _police_serif(26)
    lignes = _lignes_px(titre, f_t, PW - 170)
    h_tot = len(lignes) * 66
    y = PH // 2 - h_tot // 2 - 40
    for l in lignes:
        d.text(((PW - f_t.getlength(l)) / 2, y), l, font=f_t, fill=(230, 195, 106))
        y += 66
    d.line([(PW // 2 - 90, y + 20), (PW // 2 + 90, y + 20)], fill=(217, 180, 90), width=2)
    nom = auteur.upper()
    d.text(((PW - f_a.getlength(nom)) / 2, y + 50), nom, font=f_a, fill=(230, 211, 160))
    return im.convert("RGBA")


def _fond_livre(couleur, titre):
    from PIL import Image, ImageDraw

    base = _hex_rgb(couleur)
    im = Image.new("RGB", (LARG_V, HAUT_V))
    d = ImageDraw.Draw(im)
    for y in range(HAUT_V):  # table en bois sombre
        f = 1 - 0.55 * y / HAUT_V
        d.line([(0, y), (LARG_V, y)], fill=(int(70 * f), int(50 * f), int(34 * f)))
    d.rounded_rectangle(
        [X0 - 14, Y0 - 12, X0 + PW + 14, Y0 + PH + 14],
        radius=10,
        fill=tuple(int(c * 0.55) for c in base),
    )
    for i in range(9):  # tranche des pages
        d.line([(X0 + PW + i, Y0 + 3 + i // 2), (X0 + PW + i, Y0 + PH - 3 - i // 2)], fill=(240 - i * 6, 232 - i * 6, 210 - i * 6))
    f = _police_serif(30, True)
    t = titre if f.getlength(titre) < LARG_V - 80 else titre[:28] + "…"
    d.text(((LARG_V - f.getlength(t)) / 2, 90), t, font=f, fill=(217, 180, 90))
    return im


def _composer(fond, page):
    im = fond.copy()
    im.paste(page.convert("RGB"), (X0, Y0))
    return im


def _frame_tourne(fond, courante, verso, suivante, p):
    """Image d'une page qui se tourne (p de 0 à 1) autour de la reliure, avec perspective et ombre."""
    import math
    import numpy as np
    from PIL import Image, ImageOps

    base = _composer(fond, suivante)
    theta = math.pi * p
    c, s = math.cos(theta), math.sin(theta)
    w = int(PW * abs(c))
    if w >= 3:
        face = courante if c >= 0 else verso
        sc = face.resize((w, PH), Image.Resampling.BILINEAR).convert("RGBA")
        e = int(PH * 0.05 * s)
        tr = sc.transform(
            (w, PH), Image.Transform.QUAD, (0, -e, 0, PH + e, w, PH, w, 0),
            Image.Resampling.BILINEAR, fillcolor=(0, 0, 0, 0),
        )
        arr = np.array(tr).astype("float32")
        arr[..., :3] *= 1 - 0.45 * s
        tr = Image.fromarray(arr.clip(0, 255).astype("uint8"), "RGBA")
        if c < 0:
            tr = ImageOps.mirror(tr)
            base.paste(tr, (X0 - w, Y0), tr)
        else:
            base.paste(tr, (X0, Y0), tr)
    return np.array(base)
# --- LIVREVIDEO END ---


def fabriquer_video_livre(titre, auteur, texte, couleur="#5b2a1f", voix=True, pages_max=8):
    """Vidéo verticale : couverture, pages qui se tournent, voix off qui lit. Retourne (chemin, avertissements)."""
    import numpy as np
    from PIL import Image
    from moviepy import (
        ImageClip, VideoClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip,
    )

    avert = []
    pages = _pages_video(texte, avert)[:pages_max]
    if not pages:
        raise ValueError("Texte vide.")
    n = len(pages)
    scenes = [_rendre_couverture(titre, auteur, couleur)] + [
        _rendre_page(p["cmds"], i + 1, n) for i, p in enumerate(pages)
    ]
    narrations = [f"{titre}. Par {auteur}."] + [p["narr"] for p in pages]
    verso_couv = Image.new("RGBA", (PW, PH), (232, 220, 192, 255))
    verso_page = Image.new("RGBA", (PW, PH), (246, 241, 228, 255))
    fond = _fond_livre(couleur, titre)

    clips, audios, t = [], [], 0.0
    for i, scene in enumerate(scenes):
        duree = max(3.0, len(narrations[i].split()) * 0.4)  # durée par défaut, sans voix
        if voix:
            try:
                chemin_a = os.path.join(DOSSIER_VIDEOS, f"voix_{uuid.uuid4().hex[:6]}.mp3")
                synthese_vocale(narrations[i], chemin_a)
                a = AudioFileClip(chemin_a)
                duree = max(2.5, a.duration + 0.6)
                audios.append(a.with_start(t + 0.2))
            except Exception as e:
                avert.append(f"Voix indisponible, vidéo sans voix : {e}")
                voix = False
        clips.append(ImageClip(np.array(_composer(fond, scene))).with_duration(duree))
        t += duree
        if i < len(scenes) - 1:
            verso = verso_couv if i == 0 else verso_page
            clips.append(
                VideoClip(
                    lambda tt, c=scene, v=verso, s=scenes[i + 1]: _frame_tourne(
                        fond, c, v, s, (lambda q: q * q * (3 - 2 * q))(min(1.0, tt / 0.9))
                    ),
                    duration=0.9,
                )
            )
            t += 0.9

    final = concatenate_videoclips(clips)
    if audios:
        final = final.with_audio(CompositeAudioClip(audios))
    chemin = os.path.join(DOSSIER_VIDEOS, f"livre_{_nom_fichier(titre, 'livre')}.mp4")
    final.write_videofile(
        chemin, fps=24, codec="libx264", audio_codec="aac", logger=None
    )
    for a in audios:
        a.close()
    return chemin, avert


# =====================================================
# CHARIOW (lecture : boutique, produits, ventes) — l'API ne permet pas de créer des produits
# =====================================================
# --- CHARIOW START ---
CHARIOW_URL = "https://api.chariow.com/v1"
PERIODES_CHARIOW = {
    "aujourdhui": "today", "hier": "yesterday", "7j": "last7days", "30j": "last30days",
    "semaine": "thisWeek", "mois": "thisMonth", "mois_dernier": "lastMonth", "annee": "thisYear",
}
PERIODES_VALIDES = {
    "today", "yesterday", "last7days", "last30days", "thisWeek", "lastWeek",
    "thisMonth", "lastMonth", "thisYear", "lastYear",
}


def _periode(p):
    p = (p or "30j").strip()
    p = PERIODES_CHARIOW.get(p.lower(), p)
    return p if p in PERIODES_VALIDES else "last30days"


def chariow_get(chemin, params=None):
    cle = secret("CHARIOW_API_KEY").strip()
    if not cle:
        raise RuntimeError("Secret CHARIOW_API_KEY manquant dans Streamlit.")
    r = requests.get(
        f"{CHARIOW_URL}{chemin}",
        params=params,
        headers={"Authorization": f"Bearer {cle}"},
        timeout=20,
    )
    if r.status_code == 401:
        raise RuntimeError("Clé Chariow refusée (401) : clé fausse, supprimée ou mal copiée.")
    if r.status_code == 403:
        try:
            msg = r.json().get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"Droits insuffisants (403) : {msg} Ajoute le droit dans Chariow, Paramètres, Clés API.")
    r.raise_for_status()
    return r.json()


def resume_boutique():
    d = chariow_get("/store").get("data", {})
    clients = d.get("stats", {}).get("total_customers")
    return (
        f"Boutique : {d.get('name')} (statut : {d.get('status')})\n"
        f"Adresse : {d.get('url')}\n"
        f"Clients : {clients}"
    )


def resume_produits(max_pages=3):
    produits, curseur = [], None
    for _ in range(max_pages):
        params = {"per_page": 50}
        if curseur:
            params["cursor"] = curseur
        rep = chariow_get("/products", params)
        produits += rep.get("data", [])
        pag = rep.get("pagination", {})
        curseur = pag.get("next_cursor")
        if not pag.get("has_more_pages") or not curseur:
            break
    if not produits:
        return "Aucun produit dans la boutique Chariow."
    lignes = []
    for p in produits:
        prix = "gratuit" if p.get("is_free") else (
            (p.get("pricing") or {}).get("effective", {}).get("formatted") or "prix inconnu"
        )
        ventes = (p.get("sales_count") or {}).get("value", 0)
        lignes.append(
            f"- {p.get('name')} | type : {p.get('type')} | statut : {p.get('status')} | "
            f"prix : {prix} | ventes : {ventes}"
        )
    return f"{len(produits)} produit(s) :\n" + "\n".join(lignes)


def resume_ventes(periode="30j"):
    per = _periode(periode)
    ventes, curseur, plus = [], None, False
    for _ in range(5):
        params = {"period": per, "per_page": 100}
        if curseur:
            params["cursor"] = curseur
        rep = chariow_get("/sales", params)
        ventes += rep.get("data", [])
        pag = rep.get("pagination", {})
        curseur = pag.get("next_cursor")
        plus = bool(pag.get("has_more_pages") and curseur)
        if not plus:
            break
    if not ventes:
        return f"Aucune vente sur la période ({per})."
    statuts, revenus, par_produit = {}, {}, {}
    for v in ventes:
        st_v = v.get("status", "?")
        statuts[st_v] = statuts.get(st_v, 0) + 1
        if st_v in ("completed", "settled"):
            mont = v.get("amount") or {}
            devise = mont.get("currency", "?")
            revenus[devise] = revenus.get(devise, 0) + (mont.get("value") or 0)
            nom = (v.get("product") or {}).get("name", "?")
            par_produit[nom] = par_produit.get(nom, 0) + 1
    payees = statuts.get("completed", 0) + statuts.get("settled", 0)
    txt = [f"Période : {per}", f"Ventes payées : {payees} sur {len(ventes)} commandes"]
    if revenus:
        txt.append("Revenus : " + ", ".join(f"{s:,.2f} {d}".replace(",", " ") for d, s in revenus.items()))
    if par_produit:
        txt.append("Par produit : " + ", ".join(f"{n} ({c})" for n, c in sorted(par_produit.items(), key=lambda x: -x[1])))
    autres = {k: c for k, c in statuts.items() if k not in ("completed", "settled")}
    if autres:
        txt.append("Autres statuts : " + ", ".join(f"{k} ({c})" for k, c in autres.items()))
    if plus:
        txt.append("(Plus de 500 commandes : résultat partiel.)")
    return "\n".join(txt)
# --- CHARIOW END ---


# =====================================================
# PUBLICATION : YOUTUBE SHORTS
# =====================================================
def publier_youtube(chemin, titre, description, confidentialite=None):
    cid = secret("YT_CLIENT_ID")
    csec = secret("YT_CLIENT_SECRET")
    rtok = secret("YT_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        return "YouTube : secrets YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN manquants."
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = Credentials(
            None,
            refresh_token=rtok,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=csec,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        desc = description if "#shorts" in description.lower() else description + "\n#Shorts"
        corps = {
            "snippet": {"title": titre[:100], "description": desc, "categoryId": "22"},
            "status": {
                "privacyStatus": confidentialite or secret("YT_PRIVACY", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(chemin, mimetype="video/mp4", resumable=True)
        req = yt.videos().insert(part="snippet,status", body=corps, media_body=media)
        rep = None
        while rep is None:
            _, rep = req.next_chunk()
        return f"YouTube : envoyé (https://youtube.com/shorts/{rep['id']})"
    except Exception as e:
        return f"YouTube : échec ({e})"


# =====================================================
# PUBLICATION : TIKTOK
# =====================================================
def publier_tiktok(chemin, titre):
    acces = secret("TIKTOK_ACCESS_TOKEN")
    ckey = secret("TIKTOK_CLIENT_KEY")
    csec = secret("TIKTOK_CLIENT_SECRET")
    rtok = secret("TIKTOK_REFRESH_TOKEN")
    if not (acces or (ckey and csec and rtok)):
        return "TikTok : secrets manquants (TIKTOK_ACCESS_TOKEN ou CLIENT_KEY/CLIENT_SECRET/REFRESH_TOKEN)."
    try:
        # Les jetons d'accès TikTok expirent vite : on en demande un neuf si possible
        if ckey and csec and rtok:
            r = requests.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={
                    "client_key": ckey,
                    "client_secret": csec,
                    "grant_type": "refresh_token",
                    "refresh_token": rtok,
                },
                timeout=20,
            )
            if r.ok and r.json().get("access_token"):
                acces = r.json()["access_token"]
                nouveau = r.json().get("refresh_token")
                if nouveau and nouveau != rtok:
                    send_telegram(
                        "⚠️ TikTok a émis un nouveau refresh token. "
                        "Mets à jour TIKTOK_REFRESH_TOKEN dans les secrets (régénère-le si besoin)."
                    )
        taille = os.path.getsize(chemin)
        init = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {acces}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": titre[:2200],
                    # Une app TikTok non auditée ne peut publier qu'en SELF_ONLY (privé)
                    "privacy_level": secret("TIKTOK_PRIVACY", "SELF_ONLY"),
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": taille,
                    "chunk_size": taille,
                    "total_chunk_count": 1,
                },
            },
            timeout=30,
        )
        data = init.json()
        if not init.ok or data.get("error", {}).get("code") not in (None, "ok"):
            return f"TikTok : échec init ({init.text})"
        upload_url = data["data"]["upload_url"]
        with open(chemin, "rb") as f:
            up = requests.put(
                upload_url,
                data=f,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(taille),
                    "Content-Range": f"bytes 0-{taille - 1}/{taille}",
                },
                timeout=300,
            )
        return "TikTok : envoyé." if up.ok else f"TikTok : échec upload ({up.text})"
    except Exception as e:
        return f"TikTok : échec ({e})"


# =====================================================
# FILE D'ATTENTE DES PUBLICATIONS (validation du patron)
# =====================================================
@st.cache_resource
def get_pending():
    return {}


PENDING = get_pending()


def executer_publication(pub_id):
    pub = PENDING.pop(pub_id, None)
    if not pub:
        return "Aucune publication en attente avec cet ID."
    resultats = []
    plateformes = pub["plateformes"]
    if "youtube" in plateformes:
        resultats.append(publier_youtube(pub["video"], pub["titre"], pub["description"]))
    if "tiktok" in plateformes:
        resultats.append(publier_tiktok(pub["video"], pub["titre"] + "\n" + pub["description"]))
    if not resultats:
        resultats.append("Aucune plateforme reconnue (youtube, tiktok).")
    return "\n".join(resultats)


# =====================================================
# OUTILS DES AGENTS
# =====================================================
@tool("Demander un outil manquant")
def demander_outil_manquant(outil: str) -> str:
    """Envoie un message sur Telegram pour demander une clé API ou un outil manquant au patron."""
    msg = (
        f"🚨 ALERTE AGENT : J'ai besoin de l'outil/clé suivant : {outil}. "
        "Ajoute-le dans les secrets Streamlit !"
    )
    ok, detail = send_telegram(msg)
    return "Message envoyé au patron." if ok else f"Échec de l'envoi : {detail}"


@tool("Creer une video verticale")
def creer_video(texte: str, nom: str = "video", voix_off: bool = False) -> str:
    """Crée une vidéo verticale 9:16 (reel/short/TikTok). 'texte' = une phrase par ligne, une ligne par scène (12 max). Si voix_off est vrai, une voix lit chaque phrase. Retourne le chemin du fichier mp4."""
    try:
        return fabriquer_video(texte, nom, voix=voix_off)
    except Exception as e:
        return f"Échec de création de la vidéo : {e}"


@tool("Proposer une publication au patron")
def proposer_publication(video: str, titre: str, description: str, plateformes: str) -> str:
    """Envoie la vidéo au patron sur Telegram pour validation. 'video' = chemin du mp4 renvoyé par l'outil de création, 'plateformes' = youtube, tiktok ou youtube,tiktok. Ne publie RIEN tant que le patron n'a pas validé."""
    if not os.path.exists(video):
        return "Vidéo introuvable : utilise d'abord l'outil de création de vidéo."
    pub_id = uuid.uuid4().hex[:6]
    PENDING[pub_id] = {
        "video": video,
        "titre": titre,
        "description": description,
        "plateformes": [p.strip().lower() for p in plateformes.split(",")],
    }
    send_telegram_video(
        video,
        f"📹 Proposition {pub_id}\nTitre : {titre}\nPlateformes : {plateformes}\n\n"
        f"✅ /valider {pub_id}\n❌ /refuser {pub_id}",
    )
    return f"Proposition {pub_id} envoyée au patron. Attendre sa validation, ne publie rien."


@tool("Creer un ebook 3D")
def creer_ebook_3d(titre: str, auteur: str, texte: str, couleur: str = "#5b2a1f") -> str:
    """Crée un livre 3D réaliste à feuilleter (HTML lisible sur téléphone, tablette et ordinateur). 'texte' : une ligne '# Titre' pour chaque chapitre, '## Titre' pour un sous-titre, puis des paragraphes (un par ligne). Pour une image, une ligne '![légende](source)' où source = une URL https, ou 'ia:description de l'image' (image générée), ou 'fichier:nom.jpg' (image envoyée par le patron). 'couleur' : couverture au format #RRGGBB. Le fichier est envoyé au patron sur Telegram."""
    try:
        chemin, avert = fabriquer_ebook_3d(titre, auteur, texte, couleur)
        note = (" Avertissements : " + " | ".join(avert)) if avert else ""
        return f"Ebook créé : {chemin} ({livrer(chemin, f'📖 {titre}')}).{note}"
    except Exception as e:
        return f"Échec de création de l'ebook : {e}"


@tool("Creer video et ebook 3D")
def creer_video_et_ebook(titre: str, auteur: str, texte: str, couleur: str = "#5b2a1f", voix_off: bool = True, pages_max: int = 8) -> str:
    """Crée EN UNE FOIS l'ebook 3D (fichier HTML) ET une vidéo verticale où le livre s'ouvre, se feuillette, avec une voix off qui lit le texte. 'texte' : même format que l'outil ebook ('# Chapitre', '## Sous-titre', paragraphes, images '![légende](source)'). 'pages_max' limite la durée de la vidéo (8 pages = environ 2 à 3 minutes). Envoie les deux fichiers au patron sur Telegram. Le chemin de la vidéo peut ensuite être donné à l'outil de proposition de publication."""
    lignes = []
    try:
        chemin_livre, avert = fabriquer_ebook_3d(titre, auteur, texte, couleur)
        lignes.append(f"Ebook 3D : {chemin_livre} ({livrer(chemin_livre, f'📖 {titre}')})")
        lignes += [f"Avertissement ebook : {a}" for a in avert]
    except Exception as e:
        lignes.append(f"Échec de l'ebook : {e}")
    try:
        chemin_video, avert = fabriquer_video_livre(titre, auteur, texte, couleur, voix_off, int(pages_max))
        ok, detail = send_telegram_video(chemin_video, f"🎬 {titre}")
        etat = "envoyée sur Telegram" if ok else f"non envoyée sur Telegram ({detail})"
        lignes.append(f"Vidéo du livre : {chemin_video} ({etat})")
        lignes += [f"Avertissement vidéo : {a}" for a in avert]
    except Exception as e:
        lignes.append(f"Échec de la vidéo : {e}")
    return "\n".join(lignes)


@tool("Chariow boutique")
def chariow_boutique_outil(details: str = "") -> str:
    """Donne les informations réelles de la boutique Chariow du patron : nom, adresse (URL), statut, nombre de clients."""
    try:
        return resume_boutique()
    except Exception as e:
        return f"Chariow indisponible : {e}"


@tool("Chariow produits")
def chariow_produits_outil(details: str = "") -> str:
    """Liste les produits réels de la boutique Chariow du patron (nom, type, statut, prix, ventes). À utiliser avant de parler d'un produit : ne jamais inventer de produit ni de prix."""
    try:
        return resume_produits()
    except Exception as e:
        return f"Chariow indisponible : {e}"


@tool("Chariow ventes")
def chariow_ventes_outil(periode: str = "30j") -> str:
    """Résume les ventes réelles de la boutique Chariow sur une période : aujourdhui, hier, 7j, 30j, semaine, mois, mois_dernier, annee."""
    try:
        return resume_ventes(periode)
    except Exception as e:
        return f"Chariow indisponible : {e}"


@tool("Creer un logiciel")
def creer_logiciel(nom: str, code: str, extension: str = "html") -> str:
    """Enregistre un logiciel complet dans un seul fichier (html de préférence : fonctionne sur tous les écrans ; sinon py, js, css, json, md, txt). 'code' = le contenu COMPLET du fichier. Le fichier est envoyé au patron sur Telegram. Ne s'exécute pas sur le serveur."""
    try:
        chemin = sauver_logiciel(nom, code, extension)
        return f"Logiciel enregistré : {chemin} ({livrer(chemin, f'🧩 {nom}')})"
    except Exception as e:
        return f"Échec de l'enregistrement : {e}"


# =====================================================
# AGENTS
# =====================================================
REGLE_STRICTE = (
    "Si tu ne sais pas ou si tu n'as pas l'outil, dis-le clairement. "
    "N'invente JAMAIS de fausses données."
)


def creer_agents():
    responsable = Agent(  # pas d'outils : obligatoire pour un manager
        role="Responsable d'agence",
        goal="Découper le travail, vérifier les outils, et ne rien publier sans l'accord du patron.",
        backstory=f"Tu es le chef. Tu ne fais pas le travail, tu coordonnes. {REGLE_STRICTE}",
        llm=get_llm("gemini", 0.1),
        verbose=True,
        allow_delegation=True,
    )
    chercheur = Agent(
        role="Chercheur",
        goal="Trouver de vrais clients et analyser les tendances sans jamais inventer.",
        backstory=f"Tu es un chasseur de clients. {REGLE_STRICTE}",
        llm=get_llm("groq", 0.1),
        verbose=True,
        tools=[chariow_ventes_outil, chariow_produits_outil, demander_outil_manquant],
    )
    marketeur = Agent(
        role="Marketeur",
        goal="Rédiger des scripts de vidéos courtes percutants (une phrase courte par ligne).",
        backstory=f"Tu es un copywriter hors pair. Tu écris pour vendre. {REGLE_STRICTE}",
        llm=get_llm("groq", 0.2),
        verbose=True,
        tools=[chariow_produits_outil, chariow_boutique_outil],
    )
    codeur = Agent(
        role="Codeur",
        goal="Fabriquer les vidéos verticales, les ebooks 3D et les logiciels (fichier HTML complet) avec tes outils.",
        backstory=f"Tu produis les fichiers vidéo à partir des scripts. {REGLE_STRICTE}",
        llm=get_llm("gemini", 0.1),
        verbose=True,
        tools=[creer_video, creer_ebook_3d, creer_video_et_ebook, creer_logiciel, demander_outil_manquant],
    )
    humaniseur = Agent(
        role="Humaniseur (éditeur)",
        goal="Réécrire chaque texte pour qu'il sonne comme écrit par une vraie personne.",
        backstory=(
            "Tu es un éditeur littéraire. Tu relis tout texte destiné au public avant sa mise en forme. "
            "Règles : varie la longueur des phrases (courtes et longues), écris concret et précis, "
            "garde un ton chaleureux et une voix constante (tutoiement ou vouvoiement, pas les deux), "
            "utilise un vocabulaire du quotidien, évite les formules toutes faites des IA "
            "('dans un monde où', 'plongeons dans', 'il est important de noter', 'en conclusion'), "
            "évite les listes à puces là où une phrase suffit, et ne répète pas les mêmes débuts de phrase. "
            "Tu n'inventes JAMAIS de faux témoignages, de fausses citations ni de chiffres. "
            f"Tu gardes le sens et les faits du texte d'origine. {REGLE_STRICTE}"
        ),
        llm=get_llm("groq", 0.5),
        verbose=True,
    )
    community_manager = Agent(
        role="Community Manager",
        goal="Préparer titre, description et plateformes, puis proposer la publication au patron.",
        backstory=(
            "Tu ne publies JAMAIS directement : tu utilises uniquement l'outil de proposition "
            f"et tu attends la validation du patron. {REGLE_STRICTE}"
        ),
        llm=get_llm("openrouter", 0.2),
        verbose=True,
        tools=[proposer_publication, chariow_produits_outil, chariow_boutique_outil, demander_outil_manquant],
    )
    return responsable, chercheur, marketeur, humaniseur, codeur, community_manager


def lancer_mission(ordre_du_patron):
    responsable, chercheur, marketeur, humaniseur, codeur, community_manager = creer_agents()
    tache = Task(
        description=(
            f"Réalise la mission suivante demandée par le patron : {ordre_du_patron}\n"
            "Méthode : le Marketeur écrit, l'Humaniseur réécrit TOUT texte destiné au public, "
            "puis le Codeur le met en forme avec ses outils (vidéo, ebook 3D, logiciel). Si le patron veut un livre ET une vidéo avec voix off, utilise l'outil 'Creer video et ebook 3D'. "
            "Pour parler des produits ou des ventes du patron, utilise les outils Chariow : n'invente jamais de produit, de prix ni de chiffre. "
            "Rien n'est publié sans validation du patron."
        ),
        expected_output="Un résultat clair, basé uniquement sur des informations vérifiables.",
    )
    crew = Crew(
        agents=[chercheur, marketeur, humaniseur, codeur, community_manager],
        tasks=[tache],
        process=Process.hierarchical,
        manager_agent=responsable,
        max_rpm=10,
    )
    return str(crew.kickoff())


# =====================================================
# BOT TELEGRAM (réservé au patron)
# =====================================================
def autorise(update: Update):
    return bool(TELEGRAM_CHAT_ID) and str(update.effective_chat.id) == TELEGRAM_CHAT_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    await update.message.reply_text(
        "Bonjour patron ! Envoyez-moi un ordre.\n"
        "Pour une publication proposée : /valider ID ou /refuser ID\n"
        "Chariow : /ventes (7j, 30j, mois, hier, aujourdhui) et /produits"
    )


async def valider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    if not context.args:
        await update.message.reply_text("Utilisation : /valider ID")
        return
    await update.message.reply_text("Publication en cours...")
    res = await asyncio.to_thread(executer_publication, context.args[0])
    await update.message.reply_text(res[:4000])


async def refuser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    if not context.args:
        await update.message.reply_text("Utilisation : /refuser ID")
        return
    existe = PENDING.pop(context.args[0], None)
    await update.message.reply_text("Publication annulée." if existe else "ID inconnu.")


async def ventes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    periode = context.args[0] if context.args else "30j"
    try:
        res = await asyncio.to_thread(resume_ventes, periode)
    except Exception as e:
        res = f"Chariow : {e}"
    await update.message.reply_text(res[:4000])


async def produits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    try:
        res = await asyncio.to_thread(resume_produits)
    except Exception as e:
        res = f"Chariow : {e}"
    await update.message.reply_text(res[:4000])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    ordre_recu = update.message.text
    await update.message.reply_text(f"Ordre reçu : {ordre_recu}. Je lance l'équipe.")
    try:
        resultat = await asyncio.to_thread(lancer_mission, ordre_recu)
        for i in range(0, len(resultat), 4000):
            await update.message.reply_text(resultat[i : i + 4000])
        if secret("REPONSE_VOCALE", "oui").lower() == "oui":
            try:
                chemin_a = os.path.join(DOSSIER_VIDEOS, f"reponse_{uuid.uuid4().hex[:6]}.mp3")
                await asyncio.to_thread(synthese_vocale, resultat[:1200], chemin_a)
                await asyncio.to_thread(send_telegram_audio, chemin_a, "🔊 Réponse vocale")
            except Exception as e:
                print(f"Réponse vocale impossible : {e}")
    except Exception as e:
        await update.message.reply_text(f"Erreur : {e}")


def run_telegram_bot():
    if not TELEGRAM_TOKEN:
        return
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_app = Application.builder().token(TELEGRAM_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("valider", valider))
        bot_app.add_handler(CommandHandler("refuser", refuser))
        bot_app.add_handler(CommandHandler("ventes", ventes_cmd))
        bot_app.add_handler(CommandHandler("produits", produits_cmd))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        bot_app.run_polling(stop_signals=None, close_loop=False)
    except Exception as e:
        print(f"Erreur bot Telegram: {e}")


@st.cache_resource
def demarrer_bot():
    t = threading.Thread(target=run_telegram_bot, daemon=True)
    t.start()
    return t


demarrer_bot()

# =====================================================
# INTERFACE STREAMLIT
# =====================================================
st.title("🤖 Mon Agence d'Agents IA")
st.write("Bienvenue patron. Entrez votre ordre ci-dessous :")

ordre = st.text_input(
    "Quel est votre ordre ? (ex: Crée un short de 5 scènes sur 3 astuces fitness et propose-le)"
)

if st.button("Lancer la mission"):
    if ordre:
        with st.spinner("Les agents travaillent..."):
            try:
                resultat = lancer_mission(ordre)
                st.success("Mission terminée !")
                st.write(resultat)
                st.session_state["dernier_resultat"] = resultat
                ok, detail = send_telegram(f"✅ Mission terminée : {ordre}\n\n{resultat}")
                st.info("Résultat envoyé sur Telegram.") if ok else st.warning(f"Telegram : {detail}")
            except Exception as e:
                st.error(f"Erreur : {e}")
    else:
        st.warning("Veuillez écrire un ordre.")

if st.session_state.get("dernier_resultat"):
    if st.button("🔊 Écouter la réponse"):
        try:
            chemin_rep = os.path.join(DOSSIER_VIDEOS, "reponse_site.mp3")
            with st.spinner("Synthèse vocale..."):
                synthese_vocale(st.session_state["dernier_resultat"][:1500], chemin_rep)
            st.audio(chemin_rep)
        except Exception as e:
            st.error(f"Échec de la voix : {e}")

st.divider()
col1, col2 = st.columns(2)
with col1:
    if st.button("Tester Telegram"):
        ok, detail = send_telegram("🔔 Test : le bot fonctionne, patron !")
        st.success("Message envoyé.") if ok else st.error(f"Échec Telegram : {detail}")
with col2:
    if st.button("Tester la création de vidéo"):
        try:
            with st.spinner("Création..."):
                chemin = fabriquer_video("Bonjour patron\nCeci est un test\nÇa marche !", "test")
            st.video(chemin)
        except Exception as e:
            st.error(f"Échec : {e}")

st.divider()
if st.button("Tester l'ebook 3D"):
    try:
        from PIL import Image, ImageDraw

        img_ex = Image.new("RGB", (800, 500))
        d_ex = ImageDraw.Draw(img_ex)
        for y in range(500):
            d_ex.line([(0, y), (800, y)], fill=(60 + y // 5, 90 + y // 8, 150 - y // 6))
        d_ex.text((300, 240), "Image d'exemple", fill=(255, 255, 255))
        img_ex.save(os.path.join(DOSSIER_IMAGES, "exemple.jpg"))
    except Exception as e:
        st.warning(f"Image d'exemple non créée : {e}")
    exemple = (
        "# Chapitre 1 : Le début\n"
        "Il pleuvait ce matin-là, et personne n'avait envie de sortir. Pourtant, Léa a enfilé son manteau, "
        "attrapé son carnet et poussé la porte. Dehors, la ville sentait le café et la terre mouillée.\n"
        "Elle ne savait pas encore que cette journée allait tout changer. Elle marchait, simplement, "
        "en regardant les gens courir sous leurs parapluies.\n"
        "![Un matin de pluie](fichier:exemple.jpg)\n"
        "## Une rencontre\n"
        "Au coin de la rue, un vieux libraire installait ses caisses. Il lui a souri, sans un mot. "
        "Elle s'est arrêtée, intriguée par un livre à la couverture usée.\n"
        "# Chapitre 2 : La suite\n"
        "Le livre sentait le papier ancien. Chaque page racontait une histoire différente, "
        "écrite d'une main qu'elle ne connaissait pas. Elle a lu jusqu'à la nuit.\n"
        "Le lendemain, elle est revenue. Le libraire l'attendait avec deux tasses de thé."
    )
    st.session_state["ebook_test"] = construire_flipbook_html(
        "Mon premier livre", "Agence IA", exemple, "#5b2a1f"
    )

if "ebook_test" in st.session_state:
    st.caption("Aperçu : clique sur le livre, utilise les flèches ou glisse avec le doigt.")
    components.html(st.session_state["ebook_test"], height=720, scrolling=False)

TEXTE_EXEMPLE = (
    "# Chapitre 1 : Le début\n"
    "Il pleuvait ce matin-là, et personne n'avait envie de sortir. Pourtant, Léa a enfilé son manteau, "
    "attrapé son carnet et poussé la porte. Dehors, la ville sentait le café et la terre mouillée.\n"
    "![Un matin de pluie](fichier:exemple.jpg)\n"
    "## Une rencontre\n"
    "Au coin de la rue, un vieux libraire installait ses caisses. Il lui a souri, sans un mot.\n"
    "# Chapitre 2 : La suite\n"
    "Le livre sentait le papier ancien. Chaque page racontait une histoire différente. "
    "Elle a lu jusqu'à la nuit, puis elle est revenue le lendemain."
)


def creer_image_exemple():
    from PIL import Image, ImageDraw

    img_ex = Image.new("RGB", (800, 500))
    d_ex = ImageDraw.Draw(img_ex)
    for y in range(500):
        d_ex.line([(0, y), (800, y)], fill=(60 + y // 5, 90 + y // 8, 150 - y // 6))
    d_ex.text((300, 240), "Image d'exemple", fill=(255, 255, 255))
    img_ex.save(os.path.join(DOSSIER_IMAGES, "exemple.jpg"))


st.subheader("🔊 Voix off et vidéo du livre")
cv1, cv2 = st.columns(2)
if cv1.button("Tester la voix"):
    try:
        chemin_voix = os.path.join(DOSSIER_VIDEOS, "test_voix.mp3")
        with st.spinner("Synthèse vocale..."):
            synthese_vocale("Bonjour patron. Ceci est un test de la voix off.", chemin_voix)
        st.audio(chemin_voix)
    except Exception as e:
        st.error(f"Échec de la voix : {e}")
if cv2.button("Tester vidéo + livre + voix off"):
    try:
        creer_image_exemple()
        with st.spinner("Création du livre et de la vidéo (1 à 3 minutes)..."):
            chemin_l, av1 = fabriquer_ebook_3d("Mon premier livre", "Agence IA", TEXTE_EXEMPLE, "#5b2a1f")
            chemin_v, av2 = fabriquer_video_livre("Mon premier livre", "Agence IA", TEXTE_EXEMPLE, "#5b2a1f", True, 4)
        st.session_state["combo"] = (chemin_l, chemin_v, av1 + av2)
    except Exception as e:
        st.error(f"Échec : {e}")
if "combo" in st.session_state:
    chemin_l, chemin_v, avs = st.session_state["combo"]
    for av in avs:
        st.warning(av)
    st.video(chemin_v)
    with open(chemin_l, encoding="utf-8") as fh:
        components.html(fh.read(), height=720, scrolling=False)

st.subheader("📤 Test de publication YouTube")
st.caption("Crée une mini vidéo et l'envoie sur ta chaîne en PRIVÉ (visible par toi seul).")
if st.button("Tester l'envoi YouTube (privé)"):
    try:
        with st.spinner("Création et envoi de la vidéo..."):
            chemin_t = fabriquer_video("Test de publication\nTout fonctionne", "test_yt")
            res_yt = publier_youtube(chemin_t, "Test agence (à supprimer)", "Vidéo de test", "private")
        if "envoyé" in res_yt:
            st.success(res_yt)
        else:
            st.error(res_yt)
    except Exception as e:
        st.error(f"Échec : {e}")

st.subheader("🛒 Test Chariow")
st.caption("Lit ta boutique, tes produits et tes ventes des 30 derniers jours (lecture seule).")
if st.button("Tester Chariow"):
    for titre_c, fonction_c in (
        ("Boutique", resume_boutique),
        ("Produits", resume_produits),
        ("Ventes (30 jours)", resume_ventes),
    ):
        try:
            with st.spinner(f"{titre_c}..."):
                texte_c = fonction_c()
            st.success(titre_c)
            st.text(texte_c)
        except Exception as e:
            st.error(f"{titre_c} : {e}")

st.subheader("📁 Fichiers produits")
fichiers = []
for dossier in (DOSSIER_FICHIERS, DOSSIER_VIDEOS):
    for nom_f in os.listdir(dossier):
        chemin_f = os.path.join(dossier, nom_f)
        if os.path.isfile(chemin_f):
            fichiers.append(chemin_f)
fichiers.sort(key=os.path.getmtime, reverse=True)

if not fichiers:
    st.caption("Aucun fichier pour l'instant.")
for chemin_f in fichiers[:10]:
    nom_f = os.path.basename(chemin_f)
    c1, c2 = st.columns([3, 1])
    c1.write(nom_f)
    with open(chemin_f, "rb") as fh:
        c2.download_button("Télécharger", fh.read(), file_name=nom_f, key=f"dl_{nom_f}")
    if nom_f.startswith("ebook_") and nom_f.endswith(".html"):
        if c1.checkbox("Aperçu", key=f"apercu_{nom_f}"):
            with open(chemin_f, encoding="utf-8") as fh:
                components.html(fh.read(), height=720, scrolling=False)

st.subheader("🖼️ Images pour les livres")
envoyees = st.file_uploader(
    "Ajoute tes images (png, jpg, webp)",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True,
)
for img_f in envoyees or []:
    nom_img = "".join(c for c in img_f.name if c.isalnum() or c in "-_.") or "image.jpg"
    with open(os.path.join(DOSSIER_IMAGES, nom_img), "wb") as fh:
        fh.write(img_f.getbuffer())
disponibles = sorted(os.listdir(DOSSIER_IMAGES))
if disponibles:
    st.caption("Dans un ordre, écris par exemple : « utilise l'image fichier:" + disponibles[0] + " au chapitre 1 ». Images disponibles : " + ", ".join(disponibles))
