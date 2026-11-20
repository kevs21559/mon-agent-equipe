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


def fabriquer_video(texte, nom="video", duree_scene=3):
    """Une scène par ligne de texte. Retourne le chemin du fichier mp4."""
    import numpy as np
    from PIL import Image, ImageDraw
    from moviepy import ImageClip, concatenate_videoclips, vfx

    scenes = [l.strip() for l in texte.split("\n") if l.strip()][:12]
    if not scenes:
        raise ValueError("Texte vide.")

    couleurs = [(20, 20, 40), (40, 20, 60), (10, 60, 70), (70, 30, 30)]
    largeur, hauteur = 1080, 1920
    police = _police(80)
    clips = []

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
        clip = ImageClip(np.array(img)).with_duration(duree_scene)
        try:
            clip = clip.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(0.4)])
        except Exception:
            pass
        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")
    nom_propre = "".join(c for c in nom if c.isalnum() or c in "-_") or "video"
    chemin = os.path.join(DOSSIER_VIDEOS, f"{nom_propre}_{uuid.uuid4().hex[:6]}.mp4")
    final.write_videofile(chemin, fps=24, codec="libx264", audio=False, logger=None)
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


def _charger_image(source):
    """Retourne les octets d'une image. Sources : URL http(s), 'ia:description', 'fichier:nom.jpg'."""
    source = source.strip()
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
# PUBLICATION : YOUTUBE SHORTS
# =====================================================
def publier_youtube(chemin, titre, description):
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
                "privacyStatus": secret("YT_PRIVACY", "private"),
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
def creer_video(texte: str, nom: str = "video") -> str:
    """Crée une vidéo verticale 9:16 (reel/short/TikTok). 'texte' = une phrase par ligne, une ligne par scène (12 max). Retourne le chemin du fichier mp4."""
    try:
        return fabriquer_video(texte, nom)
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
        tools=[demander_outil_manquant],
    )
    marketeur = Agent(
        role="Marketeur",
        goal="Rédiger des scripts de vidéos courtes percutants (une phrase courte par ligne).",
        backstory=f"Tu es un copywriter hors pair. Tu écris pour vendre. {REGLE_STRICTE}",
        llm=get_llm("groq", 0.2),
        verbose=True,
    )
    codeur = Agent(
        role="Codeur",
        goal="Fabriquer les vidéos verticales, les ebooks 3D et les logiciels (fichier HTML complet) avec tes outils.",
        backstory=f"Tu produis les fichiers vidéo à partir des scripts. {REGLE_STRICTE}",
        llm=get_llm("gemini", 0.1),
        verbose=True,
        tools=[creer_video, creer_ebook_3d, creer_logiciel, demander_outil_manquant],
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
        tools=[proposer_publication, demander_outil_manquant],
    )
    return responsable, chercheur, marketeur, humaniseur, codeur, community_manager


def lancer_mission(ordre_du_patron):
    responsable, chercheur, marketeur, humaniseur, codeur, community_manager = creer_agents()
    tache = Task(
        description=(
            f"Réalise la mission suivante demandée par le patron : {ordre_du_patron}\n"
            "Méthode : le Marketeur écrit, l'Humaniseur réécrit TOUT texte destiné au public, "
            "puis le Codeur le met en forme avec ses outils (vidéo, ebook 3D, logiciel). "
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
        "Pour une publication proposée : /valider ID ou /refuser ID"
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autorise(update):
        return
    ordre_recu = update.message.text
    await update.message.reply_text(f"Ordre reçu : {ordre_recu}. Je lance l'équipe.")
    try:
        resultat = await asyncio.to_thread(lancer_mission, ordre_recu)
        for i in range(0, len(resultat), 4000):
            await update.message.reply_text(resultat[i : i + 4000])
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
                ok, detail = send_telegram(f"✅ Mission terminée : {ordre}\n\n{resultat}")
                st.info("Résultat envoyé sur Telegram.") if ok else st.warning(f"Telegram : {detail}")
            except Exception as e:
                st.error(f"Erreur : {e}")
    else:
        st.warning("Veuillez écrire un ordre.")

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
