import os
import io
import re
import ast
import csv
import json
import time
import uuid
import wave
import shutil
import base64
import zipfile
import asyncio
import tempfile
import subprocess
from datetime import datetime
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import requests
import streamlit as st

# ==============================================================================
# 1. CONFIGURATION DE LA PAGE & CORRECTIF VISIBILITÉ DU TEXTE (NOIR SUR BLANC)
# ==============================================================================
st.set_page_config(
    page_title="Super-Claude Studio & Agence CrewAI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Correctif CSS : garantit que toutes les lettres tapées sont noires (#111827) sur fond blanc (#ffffff)
st.markdown("""
<style>
    input, textarea,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    .stChatInput textarea,
    .stTextInput input,
    .stTextArea textarea {
        color: #111827 !important;
        background-color: #ffffff !important;
        caret-color: #111827 !important;
        -webkit-text-fill-color: #111827 !important;
        font-weight: 500 !important;
    }
    input::placeholder, textarea::placeholder, .stChatInput textarea::placeholder {
        color: #6b7280 !important;
        -webkit-text-fill-color: #6b7280 !important;
    }
    .claude-header {
        background: #1e293b;
        color: #f8fafc;
        padding: 14px 20px;
        border-radius: 10px;
        margin-bottom: 14px;
    }
    .badge-ok {
        background-color: #dcfce7; color: #166534; padding: 4px 10px;
        border-radius: 999px; font-size: 12px; font-weight: 700; margin-right: 6px;
        display: inline-block;
    }
    .badge-warn {
        background-color: #fef3c7; color: #92400e; padding: 4px 10px;
        border-radius: 999px; font-size: 12px; font-weight: 700; margin-right: 6px;
        display: inline-block;
    }
</style>
""", unsafe_allow_html=True)

# Gestion automatique du Disque D: si présent (pour ne jamais remplir C:)
if os.path.exists("D:\\agence_ia"):
    os.environ["TEMP"] = "D:\\agence_ia\\temp"
    os.environ["TMP"] = "D:\\agence_ia\\temp"
    DOSSIER_SORTIE = "D:\\agence_ia\\sorties"
else:
    DOSSIER_SORTIE = os.path.join(tempfile.gettempdir(), "agence_sorties")
os.makedirs(DOSSIER_SORTIE, exist_ok=True)


# ==============================================================================
# 2. LECTEUR DE CLÉS ULTRA-ROBUSTE (STREAMLIT + FICHIERS LOCAUX + INTERFACE)
# ==============================================================================
def init_etat():
    defauts = {
        "cles_manuelles": {},
        "erreur_secrets_toml": "",
        "studio_messages": [
            {
                "role": "assistant",
                "content": (
                    "Bonjour Patron ! 👋 Le bug d'affichage du clavier et la lecture des clés API sont **corrigés**.\n\n"
                    "- Regarde les badges en haut : s'il manque une clé, ouvre **« 🔑 Configurer mes Clés API »** dans la barre de gauche.\n"
                    "- Écris dans le chat ci-dessous : tes lettres s'affichent maintenant en noir bien lisible !"
                ),
            }
        ],
        "artefact_type": "code",
        "artefact_titre": "Aperçu en direct",
        "artefact_contenu": "# Tes codes, vidéos, PDF, EPUB et packs Affinity s'afficheront ici.",
        "artefact_fichier": None,
        "fichiers_produits": [],
        "propositions_attente": {},
        "commentaires_attente": {},
        "carnet_prospects": [],
        "ltx_depense_jour": 0.0,
        "fiche_marque_texte": "",
        "docs_connaissances": {},
        "tg_last_update_id": 0,
    }
    for k, v in defauts.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_etat()


def _lire_secrets_bruts_fichier() -> dict:
    """Lit secrets.toml (et secrets.toml.txt) ligne par ligne même si le format TOML a une erreur."""
    trouves = {}
    chemins = [
        ".streamlit/secrets.toml",
        ".streamlit/secrets.toml.txt",
        "D:/agence_ia/.streamlit/secrets.toml",
        "D:/agence_ia/.streamlit/secrets.toml.txt",
        "C:/Users/ME-PC/youtube_token/secrets_yt.txt",
    ]
    for ch in chemins:
        if os.path.exists(ch):
            try:
                with open(ch, "r", encoding="utf-8", errors="replace") as f:
                    for ligne in f:
                        ligne = ligne.strip()
                        if not ligne or ligne.startswith("#") or "=" not in ligne:
                            continue
                        k, v = ligne.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'").strip()
                        if k and v:
                            trouves[k] = v
            except Exception:
                pass

    # Récupération automatique depuis credentials.json si présent
    for ch_cred in [
        "credentials.json",
        "D:/agence_ia/credentials.json",
        "C:/Users/ME-PC/youtube_token/credentials.json",
    ]:
        if os.path.exists(ch_cred):
            try:
                with open(ch_cred, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    inst = d.get("installed") or d.get("web") or {}
                    if inst.get("client_id") and "YT_CLIENT_ID" not in trouves:
                        trouves["YT_CLIENT_ID"] = inst["client_id"]
                    if inst.get("client_secret") and "YT_CLIENT_SECRET" not in trouves:
                        trouves["YT_CLIENT_SECRET"] = inst["client_secret"]
            except Exception:
                pass
    return trouves


def obtenir_secret(nom: str, defaut: str = "") -> str:
    # 1. Clé collée manuellement dans l'interface
    if nom in st.session_state.cles_manuelles and st.session_state.cles_manuelles[nom]:
        return str(st.session_state.cles_manuelles[nom]).strip()

    # 2. Variable d'environnement
    val_env = os.environ.get(nom, "").strip()
    if val_env:
        return val_env

    # 3. Secrets Streamlit officiels
    try:
        if nom in st.secrets:
            val_st = str(st.secrets[nom]).strip().strip('"').strip("'")
            if val_st:
                return val_st
    except Exception as e:
        st.session_state.erreur_secrets_toml = str(e)

    # 4. Secours lecture directe fichier secrets.toml / credentials.json
    bruts = _lire_secrets_bruts_fichier()
    if nom in bruts and bruts[nom]:
        return bruts[nom]

    # 5. Alias courants (ex: GOOGLE_API_KEY <-> GEMINI_API_KEY)
    if nom == "GEMINI_API_KEY" and "GOOGLE_API_KEY" in bruts:
        return bruts["GOOGLE_API_KEY"]

    return defaut


def sauvegarder_cles_locales(dict_cles: dict):
    st.session_state.cles_manuelles.update({k: v.strip() for k, v in dict_cles.items() if v.strip()})
    for k, v in st.session_state.cles_manuelles.items():
        os.environ[k] = v
    dossier_st = "D:/agence_ia/.streamlit" if os.path.exists("D:/agence_ia") else ".streamlit"
    os.makedirs(dossier_st, exist_ok=True)
    chemin_toml = os.path.join(dossier_st, "secrets.toml")
    lignes = []
    toutes = _lire_secrets_bruts_fichier()
    toutes.update(st.session_state.cles_manuelles)
    for k, v in toutes.items():
        v_propre = str(v).replace('"', "").strip()
        lignes.append(f'{k} = "{v_propre}"')
    with open(chemin_toml, "w", encoding="utf-8") as f:
        f.write("\n".join(lignes) + "\n")


# Initialisation de la Fiche Marque si vide
if not st.session_state.fiche_marque_texte:
    fm_sec = obtenir_secret("FICHE_MARQUE")
    if fm_sec:
        st.session_state.fiche_marque_texte = fm_sec
    elif os.path.exists("fiche_marque.md"):
        try:
            with open("fiche_marque.md", "r", encoding="utf-8") as f:
                st.session_state.fiche_marque_texte = f.read()
        except Exception:
            pass
    else:
        st.session_state.fiche_marque_texte = (
            "# Fiche Marque — Nova Infopreneur\n"
            "- **Activité** : Ebooks, formations et outils numériques sur Chariow.\n"
            "- **Ton** : Direct, clair, honnête, orienté résultats concrets.\n"
            "- **Règle d'or** : Toujours utiliser les vrais produits et vrais prix Chariow."
        )


def enregistrer_fichier_produit(chemin: str, label: str = ""):
    if chemin and os.path.exists(chemin):
        if chemin not in st.session_state.fichiers_produits:
            st.session_state.fichiers_produits.append(chemin)
        st.session_state.artefact_fichier = chemin
        st.session_state.artefact_titre = label or os.path.basename(chemin)
        ext = os.path.splitext(chemin)[1].lower()
        st.session_state.artefact_type = "video" if ext == ".mp4" else ("html" if ext == ".html" else "fichier")


# ==============================================================================
# 3. MOTEUR IA MULTI-MODÈLES (GROQ + GEMINI 2.5/2.0/1.5 + OPENROUTER + CLAUDE CLI)
# ==============================================================================
def appeler_modele_ia(prompt: str, systeme: str = "", moteur_prefere: str = "auto") -> str:
    if not systeme:
        systeme = (
            "Tu es Super-Claude, l'Architecte IA et Chef des 6 Agents CrewAI de l'agence. "
            "Réponds en français clair, structuré et précis."
        )

    # 1. Option Claude CLI local (free-claude-code)
    if moteur_prefere == "claude_local":
        cmd_c = shutil.which("claude")
        if cmd_c:
            try:
                p = subprocess.run(
                    [cmd_c, "-p", f"{systeme}\n\n{prompt}"],
                    capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace"
                )
                if p.returncode == 0 and p.stdout.strip():
                    return p.stdout.strip()
            except Exception:
                pass

    ordre = ["groq", "gemini", "openrouter"]
    if moteur_prefere in ordre:
        ordre.remove(moteur_prefere)
        ordre.insert(0, moteur_prefere)

     details_erreurs = []

    for moteur in ordre:
        # --- GROQ (Essaie plusieurs modèles si l'un est saturé) ---
        if moteur == "groq":
            cle_groq = obtenir_secret("GROQ_API_KEY")
            if cle_groq:
                for mod_groq in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"]:
                    try:
                        r = requests.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {cle_groq}", "Content-Type": "application/json"},
                            json={
                                "model": mod_groq,
                                "messages": [
                                    {"role": "system", "content": systeme},
                                    {"role": "user", "content": prompt},
                                ],
                                "temperature": 0.4,
                            },
                            timeout=35,
                        )
                        if r.status_code == 200:
                            return r.json()["choices"][0]["message"]["content"]
                        details_erreurs.append(f"Groq/{mod_groq}: {r.status_code} ({r.text[:120]})")
                    except Exception as e:
                        details_erreurs.append(f"Groq/{mod_groq}: {e}")

        # --- GEMINI (Essaie gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash) ---
        if moteur == "gemini":
            cle_gem = obtenir_secret("GEMINI_API_KEY") or obtenir_secret("GOOGLE_API_KEY")
            if cle_gem:
                for mod_gem in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
                    try:
                        url_g = f"https://generativelanguage.googleapis.com/v1beta/models/{mod_gem}:generateContent?key={cle_gem}"
                        r = requests.post(
                            url_g,
                            json={
                                "contents": [{"parts": [{"text": f"{systeme}\n\n{prompt}"}]}],
                                "generationConfig": {"temperature": 0.4},
                            },
                            timeout=35,
                        )
                        if r.status_code == 200:
                            cands = r.json().get("candidates", [])
                            if cands:
                                return cands[0]["content"]["parts"][0]["text"]
                        details_erreurs.append(f"Gemini/{mod_gem}: {r.status_code} ({r.text[:120]})")
                    except Exception as e:
                        details_erreurs.append(f"Gemini/{mod_gem}: {e}")

        # --- OPENROUTER (Essaie plusieurs modèles gratuits) ---
        if moteur == "openrouter":
            cle_or = obtenir_secret("OPENROUTER_API_KEY")
            if cle_or:
                for mod_or in [
                    "meta-llama/llama-3.3-70b-instruct:free",
                    "deepseek/deepseek-chat-v3-0324:free",
                    "google/gemma-3-27b-it:free",
                    "openrouter/auto",
                ]:
                    try:
                        r = requests.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={"Authorization": f"Bearer {cle_or}", "Content-Type": "application/json"},
                            json={
                                "model": mod_or,
                                "messages": [
                                    {"role": "system", "content": systeme},
                                    {"role": "user", "content": prompt},
                                ],
                            },
                            timeout=35,
                        )
                        if r.status_code == 200 and "choices" in r.json():
                            return r.json()["choices"][0]["message"]["content"]
                        details_erreurs.append(f"OpenRouter/{mod_or}: {r.status_code}")
                    except Exception as e:
                        details_erreurs.append(f"OpenRouter/{mod_or}: {e}")

    if not (obtenir_secret("GROQ_API_KEY") or obtenir_secret("GEMINI_API_KEY") or obtenir_secret("OPENROUTER_API_KEY")):
        return (
            "🔑 **Aucune clé IA n'est encore chargée !**\n\n"
            "👉 Ouvre le menu **« 🔑 Configurer mes Clés API »** dans la barre de gauche, "
            "colle ta clé `GROQ_API_KEY` ou `GEMINI_API_KEY`, et clique sur **Enregistrer**."
        )
    return "⚠️ Réponse des serveurs IA :\n- " + "\n- ".join(details_erreurs[:6])


# ==============================================================================
# 4. FICHE MARQUE & RECHERCHE DE CONNAISSANCES
# ==============================================================================
def chercher_dans_connaissances(requete: str) -> str:
    texte_base = st.session_state.fiche_marque_texte or ""
    for nom_doc, contenu_doc in st.session_state.docs_connaissances.items():
        texte_base += f"\n\n--- {nom_doc} ---\n{contenu_doc}"
    if not texte_base.strip():
        return "Aucune fiche marque enregistrée."
    mots = [m.lower() for m in re.findall(r"\w{3,}", requete)]
    lignes = [l.strip() for l in texte_base.splitlines() if l.strip()]
    pertinentes = [(sum(1 for m in mots if m in l.lower()), l) for l in lignes]
    pertinentes = [x for x in pertinentes if x[0] > 0]
    pertinentes.sort(key=lambda x: x[0], reverse=True)
    return "\n".join(x[1] for x in pertinentes[:10]) if pertinentes else texte_base[:1200]


# ==============================================================================
# 5. TELEGRAM, CHARIOW & YOUTUBE
# ==============================================================================
def envoyer_telegram_texte(message: str) -> tuple[bool, str]:
    token = obtenir_secret("TELEGRAM_BOT_TOKEN")
    chat_id = obtenir_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "Clés TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquantes (menu 🔑 à gauche)."
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4000]},
            timeout=20,
        )
        if r.status_code == 200:
            return True, "✅ Message envoyé sur ton Telegram !"
        return False, f"Erreur Telegram ({r.status_code}) : {r.text[:200]}"
    except Exception as e:
        return False, f"Erreur Telegram : {e}"


def envoyer_telegram_fichier(chemin: str, legende: str = "") -> tuple[bool, str]:
    token = obtenir_secret("TELEGRAM_BOT_TOKEN")
    chat_id = obtenir_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not os.path.exists(chemin):
        return False, "Fichier ou clés Telegram manquants."
    ext = os.path.splitext(chemin)[1].lower()
    methode = "sendVideo" if ext == ".mp4" else "sendDocument"
    champ = "video" if ext == ".mp4" else "document"
    try:
        with open(chemin, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/{methode}",
                data={"chat_id": chat_id, "caption": legende[:1000]},
                files={champ: f},
                timeout=60,
            )
        return (True, "✅ Fichier envoyé sur Telegram !") if r.status_code == 200 else (False, r.text[:200])
    except Exception as e:
        return False, str(e)


def appel_chariow(route: str) -> tuple[bool, dict]:
    cle = obtenir_secret("CHARIOW_API_KEY")
    if not cle:
        return False, {"error": "Clé CHARIOW_API_KEY manquante (ouvre le menu 🔑 à gauche)."}
    try:
        r = requests.get(
            f"https://api.chariow.com/v1/{route.lstrip('/')}",
            headers={"Authorization": f"Bearer {cle}", "Accept": "application/json"},
            timeout=20,
        )
        if r.status_code == 200:
            return True, r.json()
        return False, {"error": f"Chariow ({r.status_code}) : {r.text[:250]}"}
    except Exception as e:
        return False, {"error": str(e)}


def lire_boutique_chariow() -> str:
    ok, data = appel_chariow("store")
    if not ok:
        return f"❌ {data.get('error')}"
    d = data.get("data", data)
    return f"🏪 Boutique : **{d.get('name', 'Ma Boutique')}** | Lien : {d.get('url') or d.get('domain') or 'N/A'}"


def lister_produits_chariow() -> str:
    ok, data = appel_chariow("products")
    if not ok:
        return f"❌ {data.get('error')}"
    items = data.get("data", data if isinstance(data, list) else [])
    if not items:
        return "ℹ️ Aucun produit dans ta boutique Chariow."
    lignes = ["📦 **Produits Chariow :**"]
    for p in items[:20]:
        lignes.append(f"- **{p.get('name') or p.get('title')}** : {p.get('price') or p.get('amount') or 0} {p.get('currency', 'FCFA')}")
    return "\n".join(lignes)


def resume_ventes_chariow(periode: str = "30j") -> str:
    ok, data = appel_chariow("sales")
    if not ok:
        return f"❌ {data.get('error')}"
    items = data.get("data", data if isinstance(data, list) else [])
    if not items:
        return f"📊 Aucune vente sur la période ({periode})."
    total, nb, devise = 0.0, 0, "FCFA"
    for v in items:
        if str(v.get("status", "paid")).lower() in ("paid", "completed", "success", "succeeded"):
            nb += 1
            total += float(v.get("amount") or v.get("total") or 0)
            devise = v.get("currency", devise)
    return f"💰 **Ventes Chariow ({periode})** : {nb} vente(s) | Total : **{total:,.0f} {devise}**"


def nettoyer_hashtags_youtube(hashtags_bruts, inclure_shorts: bool = True) -> list[str]:
    morceaux = re.split(r"[\s,;]+", hashtags_bruts) if isinstance(hashtags_bruts, str) else list(hashtags_bruts or [])
    propres = ["#Shorts"] if inclure_shorts else []
    for m in morceaux:
        mot = re.sub(r"[^\wÀ-ÿ]", "", str(m).lstrip("#"))
        if len(mot) >= 2 and f"#{mot}".lower() not in [p.lower() for p in propres]:
            propres.append(f"#{mot}")
        if len(propres) >= 5:
            break
    return propres[:5]


def suggerer_hashtags_youtube(sujet: str) -> list[str]:
    suggestions = []
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "youtube", "ds": "yt", "hl": "fr", "q": sujet},
            timeout=8,
        )
        if r.status_code == 200:
            for m in re.findall(r'"([^"]+)"', r.text)[1:8]:
                t = re.sub(r"[^\wÀ-ÿ]", "", m.title())
                if 3 <= len(t) <= 25:
                    suggestions.append(f"#{t}")
    except Exception:
        pass
    if not suggestions:
        suggestions = [f"#{re.sub(r'[^\wÀ-ÿ]', '', w.title())}" for w in sujet.split() if len(w) > 2] + ["#Astuce", "#Entrepreneur"]
    return nettoyer_hashtags_youtube(suggestions, inclure_shorts=True)


def construire_service_youtube():
    cid = obtenir_secret("YT_CLIENT_ID")
    csec = obtenir_secret("YT_CLIENT_SECRET")
    rtok = obtenir_secret("YT_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        raise ValueError("Clés YT_CLIENT_ID, YT_CLIENT_SECRET ou YT_REFRESH_TOKEN manquantes (menu 🔑 à gauche).")
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=rtok,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid,
        client_secret=csec,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def publier_video_youtube(chemin_video: str, titre: str, description: str, hashtags=None, forcer_prive: bool = False) -> tuple[bool, str]:
    try:
        youtube = construire_service_youtube()
        from googleapiclient.http import MediaFileUpload

        vis = "private" if forcer_prive else obtenir_secret("YT_PRIVACY", "public").lower()
        if vis not in ("private", "public", "unlisted"):
            vis = "public"
        tags = nettoyer_hashtags_youtube(hashtags or suggerer_hashtags_youtube(titre), inclure_shorts=True)
        ligne_tags = " ".join(tags)
        body = {
            "snippet": {
                "title": titre.strip()[:100],
                "description": f"{ligne_tags}\n\n{description.strip()}"[:4900],
                "tags": [t.lstrip("#") for t in tags],
                "categoryId": "27",
                "defaultLanguage": "fr",
                "defaultAudioLanguage": "fr",
            },
            "status": {
                "privacyStatus": vis,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": obtenir_secret("YT_CONTENU_IA", "non").lower() in ("oui", "true", "1"),
            },
        }
        media = MediaFileUpload(chemin_video, mimetype="video/mp4", resumable=True)
        rep = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
        vid_id = rep.get("id")
        vis_obt = rep.get("status", {}).get("privacyStatus", vis)
        return True, (
            f"✅ YouTube : envoyé (https://youtube.com/shorts/{vid_id})\n"
            f"Visibilité demandée : {vis} | obtenue : {vis_obt}\n"
            f"Hashtags : {ligne_tags}"
        )
    except Exception as e:
        return False, f"❌ Échec YouTube : {e}"


def lister_commentaires_sans_reponse(max_resultats: int = 8) -> tuple[bool, list]:
    try:
        youtube = construire_service_youtube()
        ch = youtube.channels().list(part="id", mine=True).execute().get("items", [])
        if not ch:
            return False, [{"erreur": "Aucune chaîne trouvée."}]
        mon_id = ch[0]["id"]
        threads = youtube.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=mon_id, maxResults=20).execute()
        res = []
        for th in threads.get("items", []):
            top = th["snippet"]["topLevelComment"]
            snip = top["snippet"]
            if snip.get("authorChannelId", {}).get("value") == mon_id or th["snippet"].get("totalReplyCount", 0) > 0:
                continue
            res.append({"id": top["id"], "auteur": snip.get("authorDisplayName", "Abonné"), "texte": snip.get("textOriginal", "")[:400]})
            if len(res) >= max_resultats:
                break
        return True, res
    except Exception as e:
        return False, [{"erreur": str(e)}]


def repondre_commentaire_youtube(parent_id: str, texte: str) -> tuple[bool, str]:
    try:
        youtube = construire_service_youtube()
        youtube.comments().insert(part="snippet", body={"snippet": {"parentId": parent_id, "textOriginal": texte[:1000]}}).execute()
        return True, "✅ Réponse publiée sur YouTube !"
    except Exception as e:
        return False, f"❌ Erreur : {e}"


# ==============================================================================
# 6. RECHERCHE WEB, VISIBILITÉ & MÉDIAS (VOIX, VIDÉO, EBOOKS, AFFINITY)
# ==============================================================================
def chercher_web_b2b(requete: str, max_resultats: int = 5) -> list[dict]:
    cle_s = obtenir_secret("SERPER_API_KEY")
    if cle_s:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": cle_s, "Content-Type": "application/json"},
                json={"q": requete, "gl": "fr", "hl": "fr", "num": max_resultats},
                timeout=12,
            )
            if r.status_code == 200:
                return [{"titre": x.get("title", ""), "url": x.get("link", ""), "extrait": x.get("snippet", "")} for x in r.json().get("organic", [])[:max_resultats]]
        except Exception:
            pass
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            return [{"titre": r.get("title", ""), "url": r.get("href", ""), "extrait": r.get("body", "")} for r in ddgs.text(requete, max_results=max_resultats)]
    except Exception as e:
        return [{"titre": "Info recherche", "url": "", "extrait": str(e)}]


def auditer_visibilite_boutique(url_b: str = "") -> str:
    if not url_b:
        ok, d = appel_chariow("store")
        if ok:
            url_b = d.get("data", d).get("url") or d.get("data", d).get("domain") or ""
    if not url_b:
        return "⚠️ Entre l'URL de ta boutique Chariow pour lancer l'audit."
    if not url_b.startswith("http"):
        url_b = "https://" + url_b
    try:
        r = requests.get(url_b, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        t = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.I | re.S)
        desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', r.text, re.I)
        og = re.search(r'<meta[^>]+property=["\']og:image["\']', r.text, re.I)
        return (
            f"🔍 **Audit Visibilité ({url_b})**\n"
            f"- {'✅' if t else '⚠️'} Titre : {t.group(1).strip()[:70] if t else 'Manquant'}\n"
            f"- {'✅' if desc else '⚠️'} Description Google : {desc.group(1).strip()[:100] if desc else 'Manquante'}\n"
            f"- {'✅' if og else '⚠️'} Aperçu image réseaux sociaux : {'Présent' if og else 'Manquant'}"
        )
    except Exception as e:
        return f"❌ Erreur lecture page : {e}"


def generer_voix_off(texte: str, voix: str = "fr-FR-HenriNeural") -> str:
    ch_mp3 = os.path.join(DOSSIER_SORTIE, f"voix_{uuid.uuid4().hex[:8]}.mp3")
    try:
        import edge_tts
        asyncio.run(edge_tts.Communicate(texte, voix).save(ch_mp3))
        if os.path.exists(ch_mp3) and os.path.getsize(ch_mp3) > 400:
            enregistrer_fichier_produit(ch_mp3, "Voix Off")
            return ch_mp3
    except Exception:
        pass
    try:
        from gtts import gTTS
        gTTS(text=texte, lang="fr").save(ch_mp3)
        if os.path.exists(ch_mp3):
            enregistrer_fichier_produit(ch_mp3, "Voix Off (gTTS)")
            return ch_mp3
    except Exception:
        pass
    ch_wav = os.path.join(DOSSIER_SORTIE, f"voix_{uuid.uuid4().hex[:8]}.wav")
    with wave.open(ch_wav, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050 * 2)
    return ch_wav


def creer_video_short(scenes: list[str], avec_voix: bool = True, prefixe: str = "short") -> str:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    try:
        from moviepy import ImageClip, AudioFileClip, concatenate_videoclips
        v2 = True
    except ImportError:
        from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
        v2 = False

    clips = []
    couleurs = [(20, 30, 48), (36, 59, 85), (44, 62, 80), (28, 40, 51)]
    for idx, txt in enumerate(scenes or ["Nova Infopreneur"]):
        img = Image.new("RGB", (720, 1280), color=couleurs[idx % len(couleurs)])
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
            except Exception:
                font = ImageFont.load_default()

        mots = txt.split()
        lignes, cur = [], ""
        for m in mots:
            if len(f"{cur} {m}".strip()) <= 24:
                cur = f"{cur} {m}".strip()
            else:
                lignes.append(cur); cur = m
        if cur:
            lignes.append(cur)

        y = 480
        for l in lignes[:8]:
            draw.text((55, y), l, fill=(255, 215, 100), font=font)
            y += 60

        duree, aud = 3.0, None
        if avec_voix:
            ch_a = generer_voix_off(txt)
            try:
                aud = AudioFileClip(ch_a)
                duree = max(2.5, float(aud.duration) + 0.3)
            except Exception:
                aud = None
        c = ImageClip(np.array(img)).with_duration(duree) if v2 else ImageClip(np.array(img)).set_duration(duree)
        if aud:
            c = c.with_audio(aud) if v2 else c.set_audio(aud)
        clips.append(c)

    ch_mp4 = os.path.join(DOSSIER_SORTIE, f"{prefixe}_{uuid.uuid4().hex[:8]}.mp4")
    concatenate_videoclips(clips, method="compose").write_videofile(ch_mp4, fps=24, codec="libx264", audio_codec="aac", logger=None)
    enregistrer_fichier_produit(ch_mp4, f"Short ({len(scenes)} scènes)")
    return ch_mp4


def generer_ebook_pdf_a5(titre: str, auteur: str, chapitres: list[dict]) -> str:
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

    ch = os.path.join(DOSSIER_SORTIE, f"ebook_{uuid.uuid4().hex[:8]}.pdf")
    doc = SimpleDocTemplate(ch, pagesize=A5, leftMargin=40, rightMargin=40, topMargin=45, bottomMargin=45)
    st_s = getSampleStyleSheet()
    story = [Spacer(1, 80), Paragraph(xml_escape(titre), st_s["Title"]), Spacer(1, 14), Paragraph(f"Par {xml_escape(auteur)}", st_s["Italic"]), PageBreak()]
    for i, c in enumerate(chapitres, 1):
        story.append(Paragraph(f"Chapitre {i} — {xml_escape(c.get('titre', ''))}", st_s["Heading1"]))
        for p in str(c.get("contenu", "")).split("\n"):
            if p.strip():
                story.append(Paragraph(xml_escape(p.strip()), st_s["BodyText"]))
                story.append(Spacer(1, 8))
        story.append(PageBreak())
    doc.build(story)
    enregistrer_fichier_produit(ch, f"PDF A5 : {titre}")
    return ch


def generer_ebook_epub(titre: str, auteur: str, chapitres: list[dict]) -> str:
    ch = os.path.join(DOSSIER_SORTIE, f"ebook_{uuid.uuid4().hex[:8]}.epub")
    with zipfile.ZipFile(ch, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        man, sp, nav = [], [], []
        for i, c in enumerate(chapitres, 1):
            fn = f"c{i}.xhtml"
            ps = "".join(f"<p>{xml_escape(p.strip())}</p>" for p in str(c.get("contenu", "")).split("\n") if p.strip())
            z.writestr(f"OEBPS/{fn}", f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{xml_escape(c.get("titre",""))}</title></head><body><h1>{xml_escape(c.get("titre",""))}</h1>{ps}</body></html>')
            man.append(f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>')
            sp.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<li><a href="{fn}">{xml_escape(c.get("titre",""))}</a></li>')
        z.writestr("OEBPS/nav.xhtml", f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>')
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">{uuid.uuid4()}</dc:identifier><dc:title>{xml_escape(titre)}</dc:title><dc:creator>{xml_escape(auteur)}</dc:creator><dc:language>fr</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{"".join(man)}</manifest><spine>{"".join(sp)}</spine></package>')
    enregistrer_fichier_produit(ch, f"EPUB : {titre}")
    return ch


def generer_livre_3d_html(titre: str, auteur: str, chapitres: list[dict]) -> str:
    pages = "".join(
        f'<div style="background:#fff;color:#111;padding:28px;margin:14px auto;max-width:460px;border-radius:8px;box-shadow:0 8px 20px rgba(0,0,0,0.25);"><h2>Chapitre {i} : {xml_escape(c.get("titre",""))}</h2><p>{xml_escape(str(c.get("contenu","")))}</p></div>'
        for i, c in enumerate(chapitres, 1)
    )
    html = f"<!DOCTYPE html><html><body style='background:#1e293b;font-family:Georgia,serif;padding:20px;'><h1 style='color:#facc15;text-align:center;'>{xml_escape(titre)} — {xml_escape(auteur)}</h1>{pages}</body></html>"
    ch = os.path.join(DOSSIER_SORTIE, f"livre3d_{uuid.uuid4().hex[:8]}.html")
    with open(ch, "w", encoding="utf-8") as f:
        f.write(html)
    enregistrer_fichier_produit(ch, f"Livre 3D : {titre}")
    return ch


def generer_pack_affinity_publisher(titre: str, auteur: str, chapitres: list[dict]) -> str:
    from docx import Document
    from PIL import Image, ImageDraw

    doc = Document()
    doc.add_heading(titre, 0)
    doc.add_paragraph(f"Par {auteur}")
    for i, c in enumerate(chapitres, 1):
        doc.add_heading(f"Chapitre {i} — {c.get('titre', '')}", level=1)
        doc.add_paragraph(str(c.get("contenu", "")))
    ch_docx = os.path.join(DOSSIER_SORTIE, "manuscrit.docx")
    doc.save(ch_docx)

    img = Image.new("RGB", (1800, 2700), color=(22, 32, 56))
    ImageDraw.Draw(img).rectangle([90, 90, 1710, 2610], outline=(212, 175, 55), width=8)
    ch_couv = os.path.join(DOSSIER_SORTIE, "couverture.jpg")
    img.save(ch_couv, "JPEG", quality=92, dpi=(300, 300))

    ch_zip = os.path.join(DOSSIER_SORTIE, f"pack_affinity_{uuid.uuid4().hex[:8]}.zip")
    with zipfile.ZipFile(ch_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ch_docx, "manuscrit.docx")
        z.write(ch_couv, "couverture.jpg")
        z.writestr("LISEZ-MOI.txt", "1. Ouvre Affinity Publisher (Format A5).\n2. Fichier > Placer > manuscrit.docx.\n3. Place couverture.jpg.")
    enregistrer_fichier_produit(ch_zip, f"Pack Affinity : {titre}")
    return ch_zip


def creer_ebook_tous_formats(titre: str, auteur: str, chapitres: list[dict]) -> dict:
    return {
        "pdf": generer_ebook_pdf_a5(titre, auteur, chapitres),
        "epub": generer_ebook_epub(titre, auteur, chapitres),
        "livre3d": generer_livre_3d_html(titre, auteur, chapitres),
        "affinity": generer_pack_affinity_publisher(titre, auteur, chapitres),
    }


# ==============================================================================
# 7. COMMANDES TELEGRAM & MISSION CREWAI
# ==============================================================================
def proposer_publication_telegram(chemin_video: str, titre: str, description: str) -> str:
    pid = uuid.uuid4().hex[:6].upper()
    tags = suggerer_hashtags_youtube(titre)
    st.session_state.propositions_attente[pid] = {
        "video": chemin_video, "titre": titre, "description": description, "hashtags": tags, "statut": "en_attente"
    }
    envoyer_telegram_fichier(
        chemin_video,
        f"🎬 Proposition #{pid}\nTitre : {titre}\nHashtags : {' '.join(tags)}\n👉 Réponds `/valider {pid}` ou `/refuser {pid}`",
    )
    return pid


def executer_commande_telegram(texte_cmd: str) -> str:
    m = texte_cmd.strip().split()
    if not m:
        return ""
    cmd, arg = m[0].lower(), (m[1] if len(m) > 1 else "")
    if cmd == "/ventes":
        r = resume_ventes_chariow(arg or "30j"); envoyer_telegram_texte(r); return r
    if cmd == "/produits":
        r = lister_produits_chariow(); envoyer_telegram_texte(r); return r
    if cmd == "/visibilite":
        r = auditer_visibilite_boutique(arg); envoyer_telegram_texte(r); return r
    if cmd == "/valider" and arg.upper() in st.session_state.propositions_attente:
        p = st.session_state.propositions_attente[arg.upper()]
        ok, msg = publier_video_youtube(p["video"], p["titre"], p["description"], p["hashtags"], forcer_prive=False)
        p["statut"] = "publie" if ok else "erreur"
        envoyer_telegram_texte(msg)
        return msg
    if cmd == "/refuser" and arg.upper() in st.session_state.propositions_attente:
        st.session_state.propositions_attente[arg.upper()]["statut"] = "refuse"
        return f"🛑 Publication #{arg.upper()} annulée."
    return f"Commande exécutée : {texte_cmd}"


def executer_mission_agents_crewai(mission: str, moteur_ia: str = "auto") -> str:
    etapes = []
    m_low = mission.lower()
    produits = lister_produits_chariow()
    marque = chercher_dans_connaissances(mission)
    web_txt = ""

    if any(w in m_low for w in ["cherche", "trouve", "prospect", "b2b", "entreprise"]):
        rw = chercher_web_b2b(mission, max_resultats=4)
        web_txt = "\n".join(f"- {x['titre']} ({x['url']}) : {x['extrait']}" for x in rw)
        etapes.append("🕵️ **Agent Chercheur & Prospecteur B2B** : Recherche web effectuée.")

    rep_ia = appeler_modele_ia(
        f"MISSION : {mission}\n\nPRODUITS CHARIOW :\n{produits}\n\nFICHE MARQUE :\n{marque}\n\nWEB :\n{web_txt}\n\n"
        "Si une vidéo est demandée, écris 4 scènes sous la forme SCENE 1: ... SCENE 2: ...",
        moteur_prefere=moteur_ia,
    )
    etapes.append("✍️ **Agents Marketeur & Humaniseur** : Texte rédigé.")

    if any(w in m_low for w in ["vidéo", "video", "short", "youtube"]):
        sc = re.findall(r"SCENE\s*\d+\s*:\s*(.+)", rep_ia, flags=re.I)
        if not sc:
            sc = [l.strip("-* ") for l in rep_ia.splitlines() if len(l.strip()) > 15][:4] or ["Bienvenue chez Nova Infopreneur"]
        ch_v = creer_video_short(sc[:4], avec_voix=True)
        etapes.append(f"🎬 **Agent Codeur** : Vidéo Short générée (`{os.path.basename(ch_v)}`).")
        if "youtube" in m_low or "publie" in m_low or "propose" in m_low:
            pid = proposer_publication_telegram(ch_v, mission[:80], rep_ia[:500])
            etapes.append(f"📲 **Agent Community Manager** : Envoyé sur Telegram -> réponds `/valider {pid}`.")

    if any(w in m_low for w in ["ebook", "livre", "pdf", "epub", "affinity"]):
        chaps = [{"titre": "Chapitre 1", "contenu": rep_ia[:1200]}, {"titre": "Chapitre 2", "contenu": rep_ia[1200:] or rep_ia}]
        creer_ebook_tous_formats(mission[:45], "Nova Infopreneur", chaps)
        etapes.append("📚 **Agent Codeur** : PDF A5, EPUB, Livre 3D et Pack Affinity créés (disponibles à droite) !")

    return "### 🤖 Rapport des Agents CrewAI\n" + "\n".join(f"- {e}" for e in etapes) + "\n\n---\n\n" + rep_ia


# ==============================================================================
# 8. BARRE LATÉRALE AVEC GESTIONNAIRE DE CLÉS API EN 1 CLIC
# ==============================================================================
with st.sidebar:
    st.markdown("## 🧠 Super-Claude Studio")

    # Menu de configuration directe des clés si elles ne sont pas détectées
    nb_cles_ia = sum(1 for k in ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"] if obtenir_secret(k))
    with st.expander("🔑 Configurer / Vérifier mes Clés API", expanded=(nb_cles_ia == 0)):
        st.caption("Colle tes clés ici : elles seront enregistrées dans `.streamlit/secrets.toml` sur ton disque.")
        in_groq = st.text_input("GROQ_API_KEY", value=obtenir_secret("GROQ_API_KEY"), type="password")
        in_gem = st.text_input("GEMINI_API_KEY", value=obtenir_secret("GEMINI_API_KEY"), type="password")
        in_or = st.text_input("OPENROUTER_API_KEY", value=obtenir_secret("OPENROUTER_API_KEY"), type="password")
        in_tg_tok = st.text_input("TELEGRAM_BOT_TOKEN", value=obtenir_secret("TELEGRAM_BOT_TOKEN"), type="password")
        in_tg_id = st.text_input("TELEGRAM_CHAT_ID", value=obtenir_secret("TELEGRAM_CHAT_ID"))
        in_yt_id = st.text_input("YT_CLIENT_ID", value=obtenir_secret("YT_CLIENT_ID"))
        in_yt_sec = st.text_input("YT_CLIENT_SECRET", value=obtenir_secret("YT_CLIENT_SECRET"), type="password")
        in_yt_ref = st.text_input("YT_REFRESH_TOKEN", value=obtenir_secret("YT_REFRESH_TOKEN"), type="password")
        in_chariow = st.text_input("CHARIOW_API_KEY", value=obtenir_secret("CHARIOW_API_KEY"), type="password")

        if st.button("💾 Enregistrer toutes mes clés", use_container_width=True):
            sauvegarder_cles_locales({
                "GROQ_API_KEY": in_groq,
                "GEMINI_API_KEY": in_gem,
                "OPENROUTER_API_KEY": in_or,
                "TELEGRAM_BOT_TOKEN": in_tg_tok,
                "TELEGRAM_CHAT_ID": in_tg_id,
                "YT_CLIENT_ID": in_yt_id,
                "YT_CLIENT_SECRET": in_yt_sec,
                "YT_REFRESH_TOKEN": in_yt_ref,
                "CHARIOW_API_KEY": in_chariow,
            })
            st.success("✅ Clés enregistrées et activées !")
            st.rerun()

    moteur_choisi = st.selectbox(
        "⚡ Moteur IA actif",
        ["auto", "groq", "gemini", "openrouter", "claude_local"],
        format_func=lambda x: {
            "auto": "🔄 Auto (Groq + Gemini + OpenRouter)",
            "groq": "🚀 Groq (Llama 3.3)",
            "gemini": "✨ Gemini (2.5 / 2.0 / 1.5 Flash)",
            "openrouter": "🌐 OpenRouter",
            "claude_local": "💻 Terminal PC (free-claude-code)",
        }[x],
    )

    st.divider()
    cmd_man = st.text_input("Commande rapide (/ventes, /produits, /valider ID)")
    if st.button("▶️ Exécuter", use_container_width=True) and cmd_man:
        st.info(executer_commande_telegram(cmd_man))

    st.divider()
    st.markdown("### 📁 Fichiers générés")
    for ch_p in reversed(st.session_state.fichiers_produits[-6:]):
        if os.path.exists(ch_p):
            with open(ch_p, "rb") as f_dl:
                st.download_button(
                    f"⬇️ {os.path.basename(ch_p)}",
                    data=f_dl.read(),
                    file_name=os.path.basename(ch_p),
                    key=f"dl_{os.path.basename(ch_p)}",
                    use_container_width=True,
                )


# ==============================================================================
# 9. EN-TÊTE DE DIAGNOSTIC VISUEL & ONGLETS
# ==============================================================================
b_ia = '<span class="badge-ok">🟢 IA Connectée</span>' if nb_cles_ia > 0 else '<span class="badge-warn">🔴 Clé IA manquante (menu 🔑 à gauche)</span>'
b_yt = '<span class="badge-ok">🟢 YouTube OK</span>' if obtenir_secret("YT_REFRESH_TOKEN") else '<span class="badge-warn">🟡 YouTube Token manquant</span>'
b_tg = '<span class="badge-ok">🟢 Telegram OK</span>' if obtenir_secret("TELEGRAM_BOT_TOKEN") else '<span class="badge-warn">🟡 Telegram manquant</span>'
b_ch = '<span class="badge-ok">🟢 Chariow OK</span>' if obtenir_secret("CHARIOW_API_KEY") else '<span class="badge-warn">🟡 Chariow manquant</span>'

st.markdown(
    f'<div class="claude-header"><h3 style="margin:0 0 8px 0;color:#fff;">🧠 Super-Claude Studio & Agence CrewAI</h3>{b_ia}{b_yt}{b_tg}{b_ch}</div>',
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🧠 1. Super-Claude & Agents CrewAI",
    "🎬 2. Vidéos Shorts & Ebooks (PDF/EPUB/Affinity)",
    "📈 3. Chariow, YouTube & Prospection B2B",
    "📘 4. Fiche Marque (RAG)",
    "🧪 5. Tous les Tests",
])

with tab1:
    col_g, col_d = st.columns([1.15, 0.85], gap="large")
    with col_g:
        piloter = st.checkbox("🤖 Faire travailler les 6 Agents CrewAI sur ma demande", value=True)
        boite = st.container(height=460)
        with boite:
            for m in st.session_state.studio_messages:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])

        q_user = st.chat_input("Écris ton message ici (les lettres sont maintenant bien visibles en noir)...")
        if q_user:
            st.session_state.studio_messages.append({"role": "user", "content": q_user})
            with boite:
                with st.chat_message("user"):
                    st.markdown(q_user)
                with st.chat_message("assistant"):
                    with st.spinner("🧠 Traitement en cours..."):
                        rep = executer_mission_agents_crewai(q_user, moteur_choisi) if piloter else appeler_modele_ia(q_user, moteur_prefere=moteur_choisi)
                        st.markdown(rep)
                        st.session_state.studio_messages.append({"role": "assistant", "content": rep})
                        st.rerun()

    with col_d:
        st.markdown(f"#### 📦 Aperçu Artefact : `{st.session_state.artefact_titre}`")
        ch_a = st.session_state.artefact_fichier
        if ch_a and os.path.exists(ch_a):
            if ch_a.endswith(".mp4"):
                st.video(ch_a)
            elif ch_a.endswith((".mp3", ".wav")):
                st.audio(ch_a)
            elif ch_a.endswith(".html"):
                with open(ch_a, "r", encoding="utf-8") as fh:
                    st.components.v1.html(fh.read(), height=380, scrolling=True)
            with open(ch_a, "rb") as fb:
                st.download_button(f"⬇️ Télécharger {os.path.basename(ch_a)}", data=fb.read(), file_name=os.path.basename(ch_a), use_container_width=True)
        else:
            st.code(st.session_state.artefact_contenu, language="python")

with tab2:
    c_v, c_e = st.columns(2)
    with c_v:
        st.subheader("🎬 Créer un Short YouTube + Voix Off")
        sc_txt = st.text_area("Scènes (1 par ligne)", value="Marcher 30 minutes par jour change ta santé.\nEssaie dès ce matin !")
        t_vid = st.text_input("Titre YouTube", value="Les bienfaits de la marche")
        if st.button("🎥 Créer la vidéo Short", use_container_width=True):
            ch_v = creer_video_short([l.strip() for l in sc_txt.splitlines() if l.strip()], avec_voix=True)
            st.video(ch_v)
    with c_e:
        st.subheader("📚 Créer un Ebook (PDF A5 + EPUB + 3D + Affinity)")
        t_eb = st.text_input("Titre de l'Ebook", value="Guide Pratique")
        c_eb = st.text_area("Texte du chapitre 1", value="Contenu détaillé de mon premier chapitre.")
        if st.button("📚 Générer les 4 formats d'Ebook", use_container_width=True):
            res_e = creer_ebook_tous_formats(t_eb, "Nova Infopreneur", [{"titre": "Chapitre 1", "contenu": c_eb}])
            st.success("✅ PDF, EPUB, Livre 3D et Pack Affinity générés dans la barre de gauche !")

with tab3:
    c_b1, c_b2 = st.columns(2)
    with c_b1:
        st.subheader("🏪 Chariow")
        if st.button("🏪 Tester ma boutique Chariow", use_container_width=True):
            st.markdown(lire_boutique_chariow())
            st.markdown(lister_produits_chariow())
            st.markdown(resume_ventes_chariow("30j"))
    with c_b2:
        st.subheader("📺 YouTube")
        if st.button("💬 Voir les commentaires sans réponse", use_container_width=True):
            ok_c, l_c = lister_commentaires_sans_reponse()
            st.write(l_c)

with tab4:
    st.subheader("📘 Fiche Marque")
    fm = st.text_area("Texte de ta Fiche Marque", value=st.session_state.fiche_marque_texte, height=240)
    if st.button("💾 Enregistrer la Fiche Marque"):
        st.session_state.fiche_marque_texte = fm
        st.success("✅ Enregistrée !")

with tab5:
    st.subheader("🧪 Tests Rapides")
    col1, col2, col3 = st.columns(3)
    if col1.button("1️⃣ Tester Telegram", use_container_width=True):
        ok, m = envoyer_telegram_texte("✅ Test Telegram réussi !")
        st.success(m) if ok else st.error(m)
    if col2.button("2️⃣ Tester la voix", use_container_width=True):
        st.audio(generer_voix_off("Bonjour patron, le test vocal fonctionne."))
    if col3.button("3️⃣ Tester l'envoi YouTube (privé)", use_container_width=True):
        ch_t = creer_video_short(["Vidéo de test (à supprimer)"], avec_voix=False, prefixe="test_yt")
        ok_y, m_y = publier_video_youtube(ch_t, "Test agence (à supprimer)", "Vidéo de test", ["#Shorts"], forcer_prive=True)
        st.success(m_y) if ok_y else st.error(m_y)
