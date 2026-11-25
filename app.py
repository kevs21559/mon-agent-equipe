
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
from datetime import datetime, timedelta
from urllib.parse import urlparse
from xml.sax.saxutils import escape as xml_escape

import requests
import streamlit as st

# ==============================================================================
# 1. CONFIGURATION DE LA PAGE & STYLE "SUPER-CLAUDE STUDIO"
# ==============================================================================
st.set_page_config(
    page_title="Super-Claude Studio & Agence CrewAI",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .stApp { background-color: #0f1117; color: #e8eaed; }
    .claude-header {
        background: linear-gradient(90deg, #1e2230 0%, #2a1f2d 100%);
        padding: 16px 22px; border-radius: 12px; border: 1px solid #383c4a;
        margin-bottom: 16px;
    }
    .badge-ok {
        background-color: #163828; color: #4ade80; padding: 4px 10px;
        border-radius: 999px; font-size: 12px; font-weight: 600; margin-right: 6px;
        border: 1px solid #22c55e55; display: inline-block;
    }
    .badge-warn {
        background-color: #3b2a14; color: #fbbf24; padding: 4px 10px;
        border-radius: 999px; font-size: 12px; font-weight: 600; margin-right: 6px;
        border: 1px solid #f59e0b55; display: inline-block;
    }
    .artifact-box {
        background-color: #161922; border: 1px solid #2e3446;
        border-radius: 12px; padding: 14px;
    }
</style>
""", unsafe_allow_html=True)

DOSSIER_SORTIE = os.path.join(tempfile.gettempdir(), "agence_sorties")
os.makedirs(DOSSIER_SORTIE, exist_ok=True)


# ==============================================================================
# 2. GESTION DES SECRETS & MÉMOIRE D'ÉTAT
# ==============================================================================
def obtenir_secret(nom: str, defaut: str = "") -> str:
    val = os.environ.get(nom, "")
    if val:
        return str(val).strip()
    try:
        if nom in st.secrets:
            return str(st.secrets[nom]).strip()
    except Exception:
        pass
    return defaut


def init_etat():
    defauts = {
        "studio_messages": [
            {
                "role": "assistant",
                "content": (
                    "Bonjour Patron ! 👋 Je suis ton **Super-Claude Studio** relié à tes **6 agents CrewAI**.\n\n"
                    "- 💬 **Discute avec moi** sans aucune limite d'heure (bascule auto Groq / Gemini / OpenRouter / Claude Local).\n"
                    "- 🤖 **Coche « Piloter les Agents CrewAI »** pour que je mobilise ton équipe (Vidéo, Ebook, Chariow, YouTube, Prospection B2B).\n"
                    "- 📦 **Regarde à droite** : tous les fichiers, codes, livres et vidéos créés s'affichent dans le panneau **Artefacts**."
                ),
            }
        ],
        "artefact_type": "code",
        "artefact_titre": "Bienvenue dans l'Aperçu en direct",
        "artefact_contenu": "# Tous les codes, PDF, EPUB, Livres 3D et Vidéos générés apparaîtront ici.",
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

    if not st.session_state.fiche_marque_texte:
        secret_fm = obtenir_secret("FICHE_MARQUE")
        if secret_fm:
            st.session_state.fiche_marque_texte = secret_fm
        elif os.path.exists("fiche_marque.md"):
            try:
                with open("fiche_marque.md", "r", encoding="utf-8") as f:
                    st.session_state.fiche_marque_texte = f.read()
            except Exception:
                pass
        else:
            st.session_state.fiche_marque_texte = (
                "# Fiche Marque — Nova Infopreneur\n"
                "- **Identité** : Création de formations, ebooks pratiques et outils numériques.\n"
                "- **Public** : Entrepreneurs, créateurs et professionnels francophones.\n"
                "- **Ton de voix** : Clair, direct, honnête, sans fausse promesse.\n"
                "- **Règles** : Toujours citer les vrais prix Chariow. Ne jamais spammer.\n"
                "- **FAQ** : Livraison immédiate par lien de téléchargement après paiement sécurisé."
            )


init_etat()


def enregistrer_fichier_produit(chemin: str, label: str = ""):
    if chemin and os.path.exists(chemin):
        if chemin not in st.session_state.fichiers_produits:
            st.session_state.fichiers_produits.append(chemin)
        ext = os.path.splitext(chemin)[1].lower()
        st.session_state.artefact_fichier = chemin
        st.session_state.artefact_titre = label or os.path.basename(chemin)
        if ext in (".mp4", ".webm"):
            st.session_state.artefact_type = "video"
        elif ext in (".html", ".htm"):
            st.session_state.artefact_type = "html"
        elif ext in (".mp3", ".wav"):
            st.session_state.artefact_type = "audio"
        else:
            st.session_state.artefact_type = "fichier"


# ==============================================================================
# 3. MOTEUR IA MULTI-MODÈLES SANS LIMITE + PONT FREE-CLAUDE-CODE LOCAL
# ==============================================================================
def appeler_claude_code_local(prompt: str) -> str:
    """Si l'utilisateur fait tourner l'app sur son PC avec free-claude-code / claude CLI installé."""
    cmd_claude = shutil.which("claude")
    if not cmd_claude:
        return ""
    try:
        proc = subprocess.run(
            [cmd_claude, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=90,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def appeler_modele_ia(prompt: str, systeme: str = "", moteur_prefere: str = "auto") -> str:
    """Appelle les modèles gratuits/configurés avec bascule automatique anti-blocage."""
    if not systeme:
        systeme = (
            "Tu es Super-Claude, l'Architecte IA et Chef des Agents CrewAI de l'utilisateur. "
            "Tu réponds en français clair, structuré, honnête, et tu peux générer du code, "
            "piloter YouTube, Telegram, Chariow, la prospection B2B et la création d'ebooks/vidéos."
        )

    # 1. Essai CLI Claude Code local si demandé
    if moteur_prefere == "claude_local":
        rep_local = appeler_claude_code_local(f"{systeme}\n\n{prompt}")
        if rep_local:
            return rep_local

    ordre = ["groq", "gemini", "openrouter"]
    if moteur_prefere in ordre:
        ordre.remove(moteur_prefere)
        ordre.insert(0, moteur_prefere)

    erreurs = []
    for moteur in ordre:
        # --- GROQ ---
        if moteur == "groq" and obtenir_secret("GROQ_API_KEY"):
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {obtenir_secret('GROQ_API_KEY')}"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [
                            {"role": "system", "content": systeme},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.4,
                    },
                    timeout=45,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"]
                erreurs.append(f"Groq ({r.status_code})")
            except Exception as e:
                erreurs.append(f"Groq ({e})")

        # --- GEMINI ---
        if moteur == "gemini" and obtenir_secret("GEMINI_API_KEY"):
            try:
                cle_gem = obtenir_secret("GEMINI_API_KEY")
                url_gem = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={cle_gem}"
                r = requests.post(
                    url_gem,
                    json={
                        "contents": [{"parts": [{"text": f"{systeme}\n\n{prompt}"}]}],
                        "generationConfig": {"temperature": 0.4},
                    },
                    timeout=45,
                )
                if r.status_code == 200:
                    cands = r.json().get("candidates", [])
                    if cands:
                        return cands[0]["content"]["parts"][0]["text"]
                erreurs.append(f"Gemini ({r.status_code})")
            except Exception as e:
                erreurs.append(f"Gemini ({e})")

        # --- OPENROUTER ---
        if moteur == "openrouter" and obtenir_secret("OPENROUTER_API_KEY"):
            try:
                r = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {obtenir_secret('OPENROUTER_API_KEY')}"},
                    json={
                        "model": "meta-llama/llama-3.3-70b-instruct:free",
                        "messages": [
                            {"role": "system", "content": systeme},
                            {"role": "user", "content": prompt},
                        ],
                    },
                    timeout=45,
                )
                if r.status_code == 200 and "choices" in r.json():
                    return r.json()["choices"][0]["message"]["content"]
                erreurs.append(f"OpenRouter ({r.status_code})")
            except Exception as e:
                erreurs.append(f"OpenRouter ({e})")

    return (
        "⚠️ Aucun modèle IA n'a pu répondre. Vérifie tes clés `GROQ_API_KEY`, `GEMINI_API_KEY` "
        f"ou `OPENROUTER_API_KEY` dans les Secrets Streamlit. Détails : {', '.join(erreurs)}"
    )


# ==============================================================================
# 4. FICHE MARQUE & RECHERCHE DE CONNAISSANCES (MINI-RAG)
# ==============================================================================
def chercher_dans_connaissances(requete: str) -> str:
    texte_base = st.session_state.fiche_marque_texte or ""
    for nom_doc, contenu_doc in st.session_state.docs_connaissances.items():
        texte_base += f"\n\n--- Document : {nom_doc} ---\n{contenu_doc}"

    if os.path.isdir("connaissances"):
        for fn in os.listdir("connaissances"):
            if fn.endswith((".md", ".txt")):
                try:
                    with open(os.path.join("connaissances", fn), "r", encoding="utf-8") as f:
                        texte_base += f"\n\n--- {fn} ---\n{f.read()}"
                except Exception:
                    pass

    if not texte_base.strip():
        return "Aucune fiche marque enregistrée."

    mots = [m.lower() for m in re.findall(r"\w{3,}", requete)]
    lignes = [l.strip() for l in texte_base.splitlines() if l.strip()]
    pertinentes = []
    for l in lignes:
        score = sum(1 for m in mots if m in l.lower())
        if score > 0:
            pertinentes.append((score, l))
    pertinentes.sort(key=lambda x: x[0], reverse=True)
    if pertinentes:
        return "\n".join(x[1] for x in pertinentes[:10])
    return texte_base[:1200]


# ==============================================================================
# 5. TELEGRAM : ENVOI DE MESSAGES, FICHIERS & LECTURE DES COMMANDES
# ==============================================================================
def envoyer_telegram_texte(message: str) -> tuple[bool, str]:
    token = obtenir_secret("TELEGRAM_BOT_TOKEN")
    chat_id = obtenir_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "Secrets TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquants."
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4000]},
            timeout=20,
        )
        if r.status_code == 200:
            return True, "Message envoyé sur Telegram."
        return False, f"Erreur Telegram ({r.status_code}) : {r.text[:200]}"
    except Exception as e:
        return False, f"Erreur réseau Telegram : {e}"


def envoyer_telegram_fichier(chemin: str, legende: str = "") -> tuple[bool, str]:
    token = obtenir_secret("TELEGRAM_BOT_TOKEN")
    chat_id = obtenir_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not os.path.exists(chemin):
        return False, "Fichier ou secrets Telegram introuvables."
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
        if r.status_code == 200:
            return True, "Fichier envoyé sur Telegram."
        return False, f"Erreur Telegram fichier : {r.text[:200]}"
    except Exception as e:
        return False, f"Erreur envoi fichier Telegram : {e}"


# ==============================================================================
# 6. CHARIOW API v1 (BOUTIQUE, PRODUITS, VENTES)
# ==============================================================================
def appel_chariow(route: str, params: dict = None) -> tuple[bool, dict]:
    cle = obtenir_secret("CHARIOW_API_KEY")
    if not cle:
        return False, {"error": "Secret CHARIOW_API_KEY manquant dans Streamlit."}
    url = f"https://api.chariow.com/v1/{route.lstrip('/')}"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {cle}", "Accept": "application/json"},
            params=params or {},
            timeout=20,
        )
        if r.status_code == 200:
            return True, r.json()
        if r.status_code in (401, 403):
            return False, {"error": f"Accès refusé Chariow ({r.status_code}) : vérifie la clé ou ses droits (store.read, products.read, sales.read)."}
        return False, {"error": f"Erreur Chariow ({r.status_code}) : {r.text[:250]}"}
    except Exception as e:
        return False, {"error": f"Erreur de connexion Chariow : {e}"}


def lire_boutique_chariow() -> str:
    ok, data = appel_chariow("store")
    if not ok:
        return f"❌ {data.get('error')}"
    d = data.get("data", data)
    nom = d.get("name", "Ma Boutique")
    url = d.get("url") or d.get("domain") or "Non renseigné"
    devise = d.get("currency", "")
    return f"🏪 Boutique Chariow : **{nom}** | Devise : {devise} | Lien : {url}"


def lister_produits_chariow() -> str:
    ok, data = appel_chariow("products")
    if not ok:
        return f"❌ {data.get('error')}"
    items = data.get("data", data if isinstance(data, list) else [])
    if not items:
        return "ℹ️ Aucun produit trouvé dans ta boutique Chariow."
    lignes = ["📦 **Produits Chariow :**"]
    for p in items[:20]:
        nom = p.get("name") or p.get("title") or "Produit sans nom"
        prix = p.get("price") or p.get("amount") or 0
        devise = p.get("currency", "FCFA")
        statut = p.get("status", "actif")
        lignes.append(f"- **{nom}** : {prix} {devise} ({statut})")
    return "\n".join(lignes)


def resume_ventes_chariow(periode: str = "30j") -> str:
    ok, data = appel_chariow("sales")
    if not ok:
        return f"❌ {data.get('error')}"
    items = data.get("data", data if isinstance(data, list) else [])
    if not items:
        return f"📊 Aucune vente enregistrée sur la période ({periode})."
    total = 0.0
    devise = "FCFA"
    par_produit = {}
    nb_payees = 0
    for v in items:
        statut = str(v.get("status", "paid")).lower()
        if statut in ("paid", "completed", "success", "succeeded"):
            nb_payees += 1
            montant = float(v.get("amount") or v.get("total") or 0)
            total += montant
            devise = v.get("currency", devise)
            nom_p = v.get("product_name") or (v.get("product") or {}).get("name") or "Produit"
            par_produit[nom_p] = par_produit.get(nom_p, 0) + 1
    details = ", ".join(f"{k}: {nb}" for k, nb in par_produit.items()) or "Détail non disponible"
    return f"💰 **Ventes Chariow ({periode})** : {nb_payees} vente(s) payée(s) | Total : **{total:,.0f} {devise}** | Par produit : {details}"


# ==============================================================================
# 7. YOUTUBE API : SHORTS PUBLICS, HASHTAGS SEO & COMMENTAIRES
# ==============================================================================
def nettoyer_hashtags_youtube(hashtags_bruts, inclure_shorts: bool = True) -> list[str]:
    if isinstance(hashtags_bruts, str):
        morceaux = re.split(r"[\s,;]+", hashtags_bruts)
    else:
        morceaux = list(hashtags_bruts or [])
    propres = []
    if inclure_shorts:
        propres.append("#Shorts")
    for m in morceaux:
        mot = re.sub(r"[^\wÀ-ÿ]", "", str(m).lstrip("#"))
        if len(mot) >= 2:
            tag = f"#{mot}"
            if tag.lower() not in [p.lower() for p in propres]:
                propres.append(tag)
        if len(propres) >= 5:
            break
    return propres[:5]


def suggerer_hashtags_youtube(sujet: str) -> list[str]:
    suggestions = []
    try:
        r = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "youtube", "ds": "yt", "hl": "fr", "q": sujet},
            timeout=10,
        )
        if r.status_code == 200:
            Texte = r.text
            mots = re.findall(r'"([^"]+)"', Texte)
            for m in mots[1:8]:
                tag_propre = re.sub(r"[^\wÀ-ÿ]", "", m.title())
                if 3 <= len(tag_propre) <= 25:
                    suggestions.append(f"#{tag_propre}")
    except Exception:
        pass
    if not suggestions:
        mots_sujet = [ re.sub(r"[^\wÀ-ÿ]", "", w.title()) for w in sujet.split() if len(w) > 2 ]
        suggestions = [f"#{w}" for w in mots_sujet] + ["#Astuce", "#Formation", "#Entrepreneur"]
    return nettoyer_hashtags_youtube(suggestions, inclure_shorts=True)


def construire_service_youtube():
    client_id = obtenir_secret("YT_CLIENT_ID")
    client_secret = obtenir_secret("YT_CLIENT_SECRET")
    refresh_token = obtenir_secret("YT_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise ValueError("Secrets manquants : vérifie YT_CLIENT_ID, YT_CLIENT_SECRET et YT_REFRESH_TOKEN.")

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def publier_video_youtube(
    chemin_video: str,
    titre: str,
    description: str,
    hashtags=None,
    forcer_prive: bool = False,
) -> tuple[bool, str]:
    try:
        youtube = construire_service_youtube()
        from googleapiclient.http import MediaFileUpload

        visibilite_demandee = "private" if forcer_prive else (obtenir_secret("YT_PRIVACY", "private").lower())
        if visibilite_demandee not in ("private", "public", "unlisted"):
            visibilite_demandee = "private"

        liste_tags = nettoyer_hashtags_youtube(hashtags or suggerer_hashtags_youtube(titre), inclure_shorts=True)
        ligne_hashtags = " ".join(liste_tags)
        desc_finale = f"{ligne_hashtags}\n\n{description.strip()}"[:4900]
        titre_propre = titre.strip()[:100]

        contenu_ia = obtenir_secret("YT_CONTENU_IA", "non").lower() in ("oui", "true", "1", "yes")

        body = {
            "snippet": {
                "title": titre_propre,
                "description": desc_finale,
                "tags": [t.lstrip("#") for t in liste_tags],
                "categoryId": "27",
                "defaultLanguage": "fr",
                "defaultAudioLanguage": "fr",
            },
            "status": {
                "privacyStatus": visibilite_demandee,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": contenu_ia,
            },
        }

        media = MediaFileUpload(chemin_video, mimetype="video/mp4", resumable=True)
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        rep = req.execute()
        vid_id = rep.get("id")
        visibilite_obtenue = rep.get("status", {}).get("privacyStatus", visibilite_demandee)
        msg = (
            f"✅ YouTube : envoyé (https://youtube.com/shorts/{vid_id})\n"
            f"Visibilité demandée : {visibilite_demandee} | obtenue : {visibilite_obtenue}\n"
            f"Hashtags : {ligne_hashtags}"
        )
        if visibilite_demandee == "public" and visibilite_obtenue != "public":
            msg += "\n⚠️ Note : Google a gardé la vidéo en privé (projet API non audité). Passe-la en Public dans YouTube Studio."
        return True, msg
    except Exception as e:
        return False, f"❌ Échec YouTube : {e}"


def lister_commentaires_sans_reponse(max_resultats: int = 8) -> tuple[bool, list]:
    try:
        youtube = construire_service_youtube()
        ch_rep = youtube.channels().list(part="id", mine=True).execute()
        items_ch = ch_rep.get("items", [])
        if not items_ch:
            return False, [{"erreur": "Aucune chaîne YouTube trouvée pour ce compte."}]
        mon_channel_id = items_ch[0]["id"]

        threads = (
            youtube.commentThreads()
            .list(part="snippet,replies", allThreadsRelatedToChannelId=mon_channel_id, maxResults=20)
            .execute()
        )
        a_traiter = []
        for th in threads.get("items", []):
            top = th["snippet"]["topLevelComment"]
            top_id = top["id"]
            snip = top["snippet"]
            auteur_id = snip.get("authorChannelId", {}).get("value", "")
            nb_reponses = th["snippet"].get("totalReplyCount", 0)
            if auteur_id == mon_channel_id or nb_reponses > 0:
                continue
            texte = snip.get("textOriginal") or snip.get("textDisplay") or ""
            auteur = snip.get("authorDisplayName", "Abonné")
            a_traiter.append({"id": top_id, "auteur": auteur, "texte": texte[:500]})
            if len(a_traiter) >= max_resultats:
                break
        return True, a_traiter
    except Exception as e:
        return False, [{"erreur": f"Erreur lecture commentaires YouTube : {e}"}]


def repondre_commentaire_youtube(parent_id: str, texte_reponse: str) -> tuple[bool, str]:
    try:
        youtube = construire_service_youtube()
        body = {"snippet": {"parentId": parent_id, "textOriginal": texte_reponse[:1000]}}
        youtube.comments().insert(part="snippet", body=body).execute()
        return True, "✅ Réponse publiée sur YouTube."
    except Exception as e:
        return False, f"❌ Erreur réponse commentaire : {e}"


# ==============================================================================
# 8. RECHERCHE WEB, AUDIT VISIBILITÉ BOUTIQUE & PROSPECTION B2B
# ==============================================================================
def chercher_web_b2b(requete: str, max_resultats: int = 5) -> list[dict]:
    cle_serper = obtenir_secret("SERPER_API_KEY")
    if cle_serper:
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": cle_serper, "Content-Type": "application/json"},
                json={"q": requete, "gl": "fr", "hl": "fr", "num": max_resultats},
                timeout=15,
            )
            if r.status_code == 200:
                org = r.json().get("organic", [])
                return [
                    {"titre": x.get("title", ""), "url": x.get("link", ""), "extrait": x.get("snippet", "")}
                    for x in org[:max_resultats]
                ]
        except Exception:
            pass

    try:
        from ddgs import DDGS
        res = []
        with DDGS() as ddgs:
            for r in ddgs.text(requete, max_results=max_resultats):
                res.append(
                    {
                        "titre": r.get("title", ""),
                        "url": r.get("href", ""),
                        "extrait": r.get("body", ""),
                    }
                )
        return res
    except Exception as e:
        return [{"titre": "Recherche indisponible", "url": "", "extrait": str(e)}]


def url_est_publique(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        h = (p.hostname or "").lower()
        if not h or h in ("localhost", "127.0.0.1", "0.0.0.0") or h.startswith(("192.168.", "10.", "172.16.")):
            return False
        return True
    except Exception:
        return False


def auditer_visibilite_boutique(url_boutique: str = "") -> str:
    if not url_boutique:
        ok, data = appel_chariow("store")
        if ok:
            d = data.get("data", data)
            url_boutique = d.get("url") or d.get("domain") or ""
    if not url_boutique:
        return "⚠️ Impossible de trouver l'adresse de ta boutique. Indique une URL (ex: https://maboutique.chariow.com)."
    if not url_boutique.startswith("http"):
        url_boutique = "https://" + url_boutique
    if not url_est_publique(url_boutique):
        return "❌ Adresse non valide ou privée."

    try:
        r = requests.get(url_boutique, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        html = r.text
        titre = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I)
        og_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']', html, re.I)

        bilan = [f"🔍 **Audit de visibilité pour {url_boutique}** :"]
        bilan.append(f"- {'✅' if titre else '⚠️'} **Titre de page** : {titre.group(1).strip()[:80] if titre else 'Absent'}")
        bilan.append(f"- {'✅' if desc else '⚠️'} **Meta Description SEO** : {desc.group(1).strip()[:120] if desc else 'Absente (recommandé : 150 caractères)'}")
        bilan.append(f"- {'✅' if og_img else '⚠️'} **Image de partage réseaux (og:image)** : {'Présente' if og_img else 'Absente'}")
        return "\n".join(bilan)
    except Exception as e:
        return f"❌ Impossible de lire la page {url_boutique} : {e}"


def enregistrer_prospect_b2b(nom: str, site: str, email: str, raison: str, message: str) -> str:
    for p in st.session_state.carnet_prospects:
        if p["site"].lower() == site.lower() or (email and p["email"].lower() == email.lower()):
            return f"ℹ️ Prospect déjà présent dans le carnet : {nom}"
    fiche = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "nom": nom,
        "site": site,
        "email": email,
        "raison": raison,
        "message": message,
    }
    st.session_state.carnet_prospects.append(fiche)
    envoyer_telegram_texte(
        f"🎯 **Nouveau Prospect B2B**\n- Nom : {nom}\n- Site : {site}\n- Email : {email or 'Non trouvé'}\n"
        f"- Raison : {raison}\n\n✉️ **Brouillon (à envoyer toi-même)** :\n{message}"
    )
    return f"✅ Prospect {nom} ajouté au carnet et envoyé sur Telegram."


def exporter_prospects_csv() -> str:
    chemin = os.path.join(DOSSIER_SORTIE, "prospects_b2b.csv")
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "nom", "site", "email", "raison", "message"])
        w.writeheader()
        for p in st.session_state.carnet_prospects:
            w.writerow(p)
    enregistrer_fichier_produit(chemin, "Carnet Prospects B2B (CSV)")
    return chemin


# ==============================================================================
# 9. MÉDIAS : VOIX OFF (EDGE-TTS / GTTS / KOKORO), VIDÉO SHORTS & LTX-2 (FAL.AI)
# ==============================================================================
def generer_voix_off(texte: str, voix: str = "fr-FR-HenriNeural") -> str:
    chemin_mp3 = os.path.join(DOSSIER_SORTIE, f"voix_{uuid.uuid4().hex[:8]}.mp3")
    # 1. Essai edge-tts
    try:
        import edge_tts

        async def _run():
            comm = edge_tts.Communicate(texte, voix)
            await comm.save(chemin_mp3)

        asyncio.run(_run())
        if os.path.exists(chemin_mp3) and os.path.getsize(chemin_mp3) > 500:
            enregistrer_fichier_produit(chemin_mp3, "Voix Off (Edge-TTS)")
            return chemin_mp3
    except Exception:
        pass

    # 2. Secours gTTS
    try:
        from gtts import gTTS
        tts = gTTS(text=texte, lang="fr")
        tts.save(chemin_mp3)
        if os.path.exists(chemin_mp3):
            enregistrer_fichier_produit(chemin_mp3, "Voix Off (gTTS)")
            return chemin_mp3
    except Exception:
        pass

    # 3. Secours WAV silence valide pour ne jamais bloquer un test
    chemin_wav = os.path.join(DOSSIER_SORTIE, f"voix_{uuid.uuid4().hex[:8]}.wav")
    with wave.open(chemin_wav, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050 * 2)
    return chemin_wav


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

    for idx, texte_scene in enumerate(scenes or ["Bienvenue chez Nova Infopreneur"]):
        img = Image.new("RGB", (720, 1280), color=couleurs[idx % len(couleurs)])
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        except Exception:
            font = ImageFont.load_default()

        # Découpage propre en lignes
        mots = texte_scene.split()
        lignes, ligne_courante = [], ""
        for m in mots:
            test = f"{ligne_courante} {m}".strip()
            if len(test) <= 24:
                ligne_courante = test
            else:
                lignes.append(ligne_courante)
                ligne_courante = m
        if ligne_courante:
            lignes.append(ligne_courante)

        y = 480
        for l in lignes[:8]:
            draw.text((60, y), l, fill=(255, 215, 100), font=font)
            y += 62

        duree = 3.0
        audio_clip = None
        if avec_voix:
            ch_audio = generer_voix_off(texte_scene)
            try:
                audio_clip = AudioFileClip(ch_audio)
                duree = max(2.5, float(audio_clip.duration) + 0.3)
            except Exception:
                audio_clip = None

        arr = np.array(img)
        c = ImageClip(arr).with_duration(duree) if v2 else ImageClip(arr).set_duration(duree)
        if audio_clip:
            c = c.with_audio(audio_clip) if v2 else c.set_audio(audio_clip)
        clips.append(c)

    final = concatenate_videoclips(clips, method="compose")
    chemin_mp4 = os.path.join(DOSSIER_SORTIE, f"{prefixe}_{uuid.uuid4().hex[:8]}.mp4")
    final.write_videofile(chemin_mp4, fps=24, codec="libx264", audio_codec="aac", logger=None)
    enregistrer_fichier_produit(chemin_mp4, f"Vidéo Short ({len(scenes)} scènes)")
    return chemin_mp4


def generer_clip_ltx_fal(prompt_en: str, secondes: int = 6) -> tuple[bool, str]:
    cle_fal = obtenir_secret("FAL_KEY")
    if not cle_fal:
        return False, "Secret FAL_KEY manquant dans Streamlit."

    max_sec = int(obtenir_secret("LTX_MAX_SECONDES", "8") or 8)
    budget_max = float(obtenir_secret("LTX_BUDGET_JOUR_USD", "3.0") or 3.0)
    secondes = max(6, min(int(secondes), max_sec))
    cout_estime = round(secondes * 0.09, 2)

    if st.session_state.ltx_depense_jour + cout_estime > budget_max:
        return False, f"🛑 Plafond journalier LTX atteint ({st.session_state.ltx_depense_jour:.2f}$ / {budget_max:.2f}$)."

    try:
        r = requests.post(
            "https://fal.run/fal-ai/ltx-video",
            headers={"Authorization": f"Key {cle_fal}", "Content-Type": "application/json"},
            json={"prompt": prompt_en},
            timeout=120,
        )
        if r.status_code != 200:
            return False, f"Erreur fal.ai ({r.status_code}) : {r.text[:250]}"
        data = r.json()
        url_vid = (data.get("video") or {}).get("url") or data.get("video_url")
        if not url_vid:
            return False, f"Réponse fal.ai sans vidéo : {str(data)[:200]}"

        rv = requests.get(url_vid, timeout=60)
        ch_mp4 = os.path.join(DOSSIER_SORTIE, f"ltx_{uuid.uuid4().hex[:8]}.mp4")
        with open(ch_mp4, "wb") as f:
            f.write(rv.content)
        st.session_state.ltx_depense_jour += cout_estime
        enregistrer_fichier_produit(ch_mp4, f"Clip IA LTX ({secondes}s)")
        return True, ch_mp4
    except Exception as e:
        return False, f"Erreur LTX fal.ai : {e}"


# ==============================================================================
# 10. EBOOKS : PDF A5 (REPORTLAB), EPUB, LIVRE 3D & PACK AFFINITY PUBLISHER
# ==============================================================================
def creer_couverture_image(titre: str, auteur: str, sous_titre: str = "") -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1800, 2700), color=(22, 32, 56))
    draw = ImageDraw.Draw(img)
    draw.rectangle([90, 90, 1710, 2610], outline=(212, 175, 55), width=8)
    try:
        f_titre = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 92)
        f_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52)
    except Exception:
        f_titre = ImageFont.load_default()
        f_sub = ImageFont.load_default()

    draw.text((180, 650), titre[:35], fill=(245, 215, 110), font=f_titre)
    if len(titre) > 35:
        draw.text((180, 770), titre[35:70], fill=(245, 215, 110), font=f_titre)
    if sous_titre:
        draw.text((180, 1000), sous_titre[:55], fill=(220, 225, 235), font=f_sub)
    draw.text((180, 2350), auteur, fill=(212, 175, 55), font=f_sub)

    ch = os.path.join(DOSSIER_SORTIE, f"couverture_{uuid.uuid4().hex[:6]}.jpg")
    img.save(ch, "JPEG", quality=92, dpi=(300, 300))
    return ch


def generer_ebook_pdf_a5(titre: str, auteur: str, chapitres: list[dict]) -> str:
    from reportlab.lib.pagesizes import A5
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

    ch_pdf = os.path.join(DOSSIER_SORTIE, f"ebook_{uuid.uuid4().hex[:8]}.pdf")
    doc = SimpleDocTemplate(
        ch_pdf, pagesize=A5, leftMargin=42, rightMargin=42, topMargin=48, bottomMargin=48
    )
    styles = getSampleStyleSheet()
    st_titre = ParagraphStyle("LivreTitre", parent=styles["Title"], fontSize=22, leading=28, textColor=colors.HexColor("#162038"))
    st_chap = ParagraphStyle("Chapitre", parent=styles["Heading1"], fontSize=16, leading=22, spaceBefore=14, spaceAfter=10, textColor=colors.HexColor("#1e293b"))
    st_corps = ParagraphStyle("Corps", parent=styles["BodyText"], fontSize=10.5, leading=15, spaceAfter=8)

    story = [
        Spacer(1, 90),
        Paragraph(xml_escape(titre), st_titre),
        Spacer(1, 18),
        Paragraph(f"Par {xml_escape(auteur)}", styles["Italic"]),
        PageBreak(),
        Paragraph("<b>Sommaire</b>", st_chap),
    ]
    for i, c in enumerate(chapitres, 1):
        story.append(Paragraph(f"{i}. {xml_escape(c.get('titre', f'Chapitre {i}'))}", st_corps))
    story.append(PageBreak())

    for i, c in enumerate(chapitres, 1):
        story.append(Paragraph(f"CHAPITRE {i} — {xml_escape(c.get('titre', ''))}", st_chap))
        for p in str(c.get("contenu", "")).split("\n"):
            if p.strip():
                story.append(Paragraph(xml_escape(p.strip()), st_corps))
        story.append(PageBreak())

    doc.build(story)
    enregistrer_fichier_produit(ch_pdf, f"Ebook PDF A5 : {titre}")
    return ch_pdf


def generer_ebook_epub(titre: str, auteur: str, chapitres: list[dict]) -> str:
    ch_epub = os.path.join(DOSSIER_SORTIE, f"ebook_{uuid.uuid4().hex[:8]}.epub")
    book_id = str(uuid.uuid4())
    with zipfile.ZipFile(ch_epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
        )
        manifest_items, spine_items, nav_links = [], [], []
        for i, c in enumerate(chapitres, 1):
            fn = f"chap_{i}.xhtml"
            paras = "\n".join(
                f"<p>{xml_escape(p.strip())}</p>"
                for p in str(c.get("contenu", "")).split("\n")
                if p.strip()
            )
            xhtml = (
                '<?xml version="1.0" encoding="utf-8"?>'
                '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="fr">'
                f"<head><title>{xml_escape(c.get('titre', ''))}</title></head>"
                f"<body><h1>{xml_escape(c.get('titre', ''))}</h1>{paras}</body></html>"
            )
            z.writestr(f"OEBPS/{fn}", xhtml)
            manifest_items.append(f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'<itemref idref="c{i}"/>')
            nav_links.append(f'<li><a href="{fn}">{xml_escape(c.get("titre", ""))}</a></li>')

        nav_xhtml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="fr">'
            "<head><title>Sommaire</title></head><body>"
            f'<nav epub:type="toc"><h1>{xml_escape(titre)}</h1><ol>{"".join(nav_links)}</ol></nav>'
            "</body></html>"
        )
        z.writestr("OEBPS/nav.xhtml", nav_xhtml)

        opf = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="uid">{book_id}</dc:identifier>'
            f"<dc:title>{xml_escape(titre)}</dc:title>"
            f"<dc:creator>{xml_escape(auteur)}</dc:creator>"
            "<dc:language>fr</dc:language>"
            "</metadata><manifest>"
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
            f'{"".join(manifest_items)}</manifest>'
            f'<spine>{"".join(spine_items)}</spine></package>'
        )
        z.writestr("OEBPS/content.opf", opf)

    enregistrer_fichier_produit(ch_epub, f"Ebook EPUB : {titre}")
    return ch_epub


def generer_livre_3d_html(titre: str, auteur: str, chapitres: list[dict]) -> str:
    pages_html = []
    for i, c in enumerate(chapitres, 1):
        paras = "".join(f"<p>{xml_escape(p)}</p>" for p in str(c.get("contenu", "")).split("\n") if p.strip())
        pages_html.append(
            f'<div class="page"><h2>Chapitre {i} : {xml_escape(c.get("titre", ""))}</h2>{paras}<div class="num">Page {i}</div></div>'
        )
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>{xml_escape(titre)}</title>
    <style>
        body {{ background:#111827; color:#1f2937; font-family:Georgia,serif; display:flex; flex-direction:column; align-items:center; padding:20px; }}
        .couverture {{ background:linear-gradient(135deg,#1e293b,#0f172a); color:#f8fafc; padding:40px; border-radius:12px; border:3px solid #d4af37; width:460px; text-align:center; margin-bottom:24px; box-shadow:0 20px 35px rgba(0,0,0,0.5); }}
        .page {{ background:#fdfbf7; width:460px; min-height:540px; padding:36px; margin-bottom:18px; border-radius:8px; box-shadow:8px 8px 24px rgba(0,0,0,0.35); position:relative; line-height:1.6; }}
        .num {{ position:absolute; bottom:14px; right:24px; font-size:12px; color:#6b7280; }}
    </style></head><body>
    <div class="couverture"><h1>{xml_escape(titre)}</h1><p>Par {xml_escape(auteur)}</p></div>
    {"".join(pages_html)}
    </body></html>"""
    ch_html = os.path.join(DOSSIER_SORTIE, f"livre3d_{uuid.uuid4().hex[:8]}.html")
    with open(ch_html, "w", encoding="utf-8") as f:
        f.write(html)
    enregistrer_fichier_produit(ch_html, f"Livre 3D : {titre}")
    return ch_html


def generer_pack_affinity_publisher(titre: str, auteur: str, chapitres: list[dict]) -> str:
    from docx import Document
    from docx.shared import Mm

    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Mm(148)
    sec.page_height = Mm(210)

    doc.add_heading(titre, level=0)
    doc.add_paragraph(f"Auteur : {auteur}")
    doc.add_page_break()

    for i, c in enumerate(chapitres, 1):
        doc.add_heading(f"Chapitre {i} — {c.get('titre', '')}", level=1)
        for p in str(c.get("contenu", "")).split("\n"):
            if p.strip():
                doc.add_paragraph(p.strip())
        doc.add_page_break()

    ch_docx = os.path.join(DOSSIER_SORTIE, "manuscrit.docx")
    doc.save(ch_docx)
    ch_couv = creer_couverture_image(titre, auteur)

    lisez_moi = (
        f"PACK AFFINITY PUBLISHER — {titre}\n"
        "==========================================\n"
        "1. Ouvre Affinity Publisher > Fichier > Nouveau (Format A5, pages en vis-à-vis).\n"
        "2. Fichier > Placer : sélectionne manuscrit.docx et clique dans ton cadre de texte.\n"
        "3. Les styles (Heading 1, Normal) sont conservés pour personnaliser tout le livre en 1 clic.\n"
        "4. Place couverture.jpg (1800x2700 px, 300 DPI) sur la page de couverture.\n"
    )
    ch_zip = os.path.join(DOSSIER_SORTIE, f"pack_affinity_{uuid.uuid4().hex[:8]}.zip")
    with zipfile.ZipFile(ch_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ch_docx, "manuscrit.docx")
        z.write(ch_couv, "couverture.jpg")
        z.writestr("LISEZ-MOI.txt", lisez_moi)

    enregistrer_fichier_produit(ch_zip, f"Pack Affinity Publisher (.zip) : {titre}")
    return ch_zip


def creer_ebook_tous_formats(titre: str, auteur: str, chapitres: list[dict], inclure_affinity: bool = True) -> dict:
    res = {
        "pdf": generer_ebook_pdf_a5(titre, auteur, chapitres),
        "epub": generer_ebook_epub(titre, auteur, chapitres),
        "livre3d": generer_livre_3d_html(titre, auteur, chapitres),
    }
    if inclure_affinity:
        res["affinity"] = generer_pack_affinity_publisher(titre, auteur, chapitres)
    return res


# ==============================================================================
# 11. CIRCUIT DE VALIDATION TELEGRAM (/valider, /refuser, /repondre, /ventes...)
# ==============================================================================
def proposer_publication_telegram(chemin_video: str, titre: str, description: str, hashtags=None) -> str:
    prop_id = uuid.uuid4().hex[:6].upper()
    tags = nettoyer_hashtags_youtube(hashtags or suggerer_hashtags_youtube(titre), inclure_shorts=True)
    vis = obtenir_secret("YT_PRIVACY", "private")
    st.session_state.propositions_attente[prop_id] = {
        "video": chemin_video,
        "titre": titre,
        "description": description,
        "hashtags": tags,
        "statut": "en_attente",
    }
    legende = (
        f"🎬 **Proposition de publication #{prop_id}**\n"
        f"- Titre : {titre}\n"
        f"- Visibilité prévue : {vis}\n"
        f"- Hashtags : {' '.join(tags)}\n\n"
        f"👉 Réponds `/valider {prop_id}` pour publier sur YouTube ou `/refuser {prop_id}`."
    )
    envoyer_telegram_fichier(chemin_video, legende)
    return prop_id


def executer_commande_telegram(texte_cmd: str) -> str:
    morceaux = texte_cmd.strip().split()
    if not morceaux:
        return ""
    cmd = morceaux[0].lower()
    arg = morceaux[1] if len(morceaux) > 1 else ""
    reste = " ".join(morceaux[2:]) if len(morceaux) > 2 else ""

    if cmd == "/ventes":
        rep = resume_ventes_chariow(arg or "30j")
        envoyer_telegram_texte(rep)
        return rep

    if cmd == "/produits":
        rep = lister_produits_chariow()
        envoyer_telegram_texte(rep)
        return rep

    if cmd == "/visibilite":
        rep = auditer_visibilite_boutique(arg)
        envoyer_telegram_texte(rep)
        return rep

    if cmd == "/prospects":
        ch = exporter_prospects_csv()
        envoyer_telegram_fichier(ch, f"📋 Carnet de prospects B2B ({len(st.session_state.carnet_prospects)} entrées)")
        return "Carnet CSV envoyé sur Telegram."

    if cmd == "/commentaires":
        ok, liste = lister_commentaires_sans_reponse()
        if not ok:
            msg = liste[0].get("erreur", "Erreur")
            envoyer_telegram_texte(msg)
            return msg
        if not liste:
            msg = "ℹ️ Aucun nouveau commentaire sans réponse sur ta chaîne."
            envoyer_telegram_texte(msg)
            return msg
        for c in liste:
            cid = uuid.uuid4().hex[:5].upper()
            extrait_fiche = chercher_dans_connaissances(c["texte"])
            brouillon = appeler_modele_ia(
                f"Commentaire abonné ({c['auteur']}) : {c['texte']}\n"
                f"Extrait Fiche Marque : {extrait_fiche}\n"
                "Rédige une réponse courte (1 à 2 phrases), chaleureuse et honnête."
            )
            st.session_state.commentaires_attente[cid] = {
                "parent_id": c["id"],
                "auteur": c["auteur"],
                "texte": c["texte"],
                "brouillon": brouillon,
            }
            envoyer_telegram_texte(
                f"💬 **Commentaire #{cid}** de {c['auteur']} :\n« {c['texte']} »\n\n"
                f"✍️ **Brouillon proposé** :\n{brouillon}\n\n"
                f"👉 `/repondre {cid}` (ou `/repondre {cid} ton texte`) | `/ignorer {cid}`"
            )
        return f"{len(liste)} commentaire(s) envoyé(s) sur Telegram."

    if cmd == "/valider" and arg.upper() in st.session_state.propositions_attente:
        prop = st.session_state.propositions_attente[arg.upper()]
        ok, msg = publier_video_youtube(
            prop["video"], prop["titre"], prop["description"], prop["hashtags"], forcer_prive=False
        )
        prop["statut"] = "publie" if ok else "erreur"
        envoyer_telegram_texte(msg)
        return msg

    if cmd == "/refuser" and arg.upper() in st.session_state.propositions_attente:
        st.session_state.propositions_attente[arg.upper()]["statut"] = "refuse"
        msg = f"🛑 Publication #{arg.upper()} annulée."
        envoyer_telegram_texte(msg)
        return msg

    if cmd == "/repondre" and arg.upper() in st.session_state.commentaires_attente:
        item = st.session_state.commentaires_attente.pop(arg.upper())
        texte_final = reste.strip() if reste.strip() else item["brouillon"]
        ok, msg = repondre_commentaire_youtube(item["parent_id"], texte_final)
        envoyer_telegram_texte(msg)
        return msg

    if cmd == "/ignorer" and arg.upper() in st.session_state.commentaires_attente:
        st.session_state.commentaires_attente.pop(arg.upper(), None)
        msg = f"🗑️ Commentaire #{arg.upper()} ignoré."
        envoyer_telegram_texte(msg)
        return msg

    return "Commande non reconnue ou ID expiré."


def verifier_messages_telegram_entrants() -> list[str]:
    token = obtenir_secret("TELEGRAM_BOT_TOKEN")
    if not token:
        return []
    actions = []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": st.session_state.tg_last_update_id + 1, "timeout": 2},
            timeout=6,
        )
        if r.status_code == 200:
            for upd in r.json().get("result", []):
                st.session_state.tg_last_update_id = upd["update_id"]
                txt = (upd.get("message") or {}).get("text", "").strip()
                if txt.startswith("/"):
                    res = executer_commande_telegram(txt)
                    actions.append(f"{txt} -> {res}")
    except Exception:
        pass
    return actions


# ==============================================================================
# 12. ORCHESTRATEUR DES 6 AGENTS CREWAI + SUPER-ARCHITECTE CLAUDE
# ==============================================================================
def executer_mission_agents_crewai(mission: str, moteur_ia: str = "auto") -> str:
    """Fait travailler les 6 agents spécialisés sur la mission demandée et produit les livrables."""
    journal = []
    m_low = mission.lower()

    # 1. Agent Chercheur & Chariow
    produits_reels = lister_produits_chariow()
    extrait_marque = chercher_dans_connaissances(mission)
    infos_web = ""
    if any(k in m_low for k in ["cherche", "trouve", "prospect", "entreprise", "b2b", "marché", "tendance"]):
        res_w = chercher_web_b2b(mission, max_resultats=4)
        infos_web = "\n".join(f"- {x['titre']} ({x['url']}) : {x['extrait']}" for x in res_w)
        journal.append("🕵️ **Agent Chercheur** : Recherche web et lecture des produits Chariow terminées.")

    # 2. Agent Prospecteur B2B (si demandé)
    if any(k in m_low for k in ["prospect", "b2b", "client", "entreprise"]):
        res_sites = chercher_web_b2b(mission, max_resultats=3)
        for s in res_sites:
            if s.get("url"):
                msg_b2b = appeler_modele_ia(
                    f"Rédige un court message de prospection B2B honnête (3 phrases max, sans spam) "
                    f"pour l'entreprise '{s['titre']}' ({s['url']}) en lien avec nos produits :\n{produits_reels}\n"
                    f"Fiche marque :\n{extrait_marque}",
                    moteur_prefere=moteur_ia,
                )
                enregistrer_prospect_b2b(s["titre"][:60], s["url"], "", s["extrait"][:140], msg_b2b)
        journal.append(f"🎯 **Agent Prospecteur B2B** : {len(res_sites)} prospect(s) analysé(s) et envoyé(s) sur Telegram.")

    # 3. Agent Marketeur + Agent Humaniseur
    Synthese = appeler_modele_ia(
        f"MISSION DE L'UTILISATEUR : {mission}\n\n"
        f"NOS VRAIS PRODUITS CHARIOW :\n{produits_reels}\n\n"
        f"NOTRE FICHE MARQUE :\n{extrait_marque}\n\n"
        f"RECHERCHE WEB :\n{infos_web}\n\n"
        "Travail demandé aux agents (Marketeur + Humaniseur + Community Manager) :\n"
        "1. Rédige une réponse complète, humaine, chaleureuse et directement utilisable.\n"
        "2. Si une vidéo est demandée, fournis 4 scènes courtes sous la forme SCENE 1: ... SCENE 2: ...\n"
        "3. Si un livre/ebook est demandé, structure-le avec CHAPITRE 1: Titre puis le texte.",
        moteur_prefere=moteur_ia,
    )
    journal.append("✍️ **Agents Marketeur & Humaniseur** : Contenu rédigé selon la Fiche Marque.")

    # 4. Agent Codeur Multimédia : Création automatique de Vidéo Short si demandée
    if any(k in m_low for k in ["vidéo", "video", "short", "youtube", "tiktok"]):
        scenes_trouvees = re.findall(r"SCENE\s*\d+\s*:\s*(.+)", Synthese, flags=re.I)
        if not scenes_trouvees:
            lignes_propres = [l.strip("-* ") for l in Synthese.splitlines() if len(l.strip()) > 15]
            scenes_trouvees = lignes_propres[:4] or ["Découvre notre méthode étape par étape."]
        ch_vid = creer_video_short(scenes_trouvees[:4], avec_voix=True, prefixe="mission")
        journal.append(f"🎬 **Agent Codeur Multimédia** : Vidéo Short créée (`{os.path.basename(ch_vid)}`).")

        if any(k in m_low for k in ["youtube", "publie", "publication", "propose"]):
            pid = proposer_publication_telegram(ch_vid, mission[:85], Synthese[:600])
            journal.append(
                f"📲 **Agent Community Manager** : Vidéo envoyée sur Telegram (ID `{pid}`). "
                f"Réponds `/valider {pid}` sur Telegram pour la publier sur YouTube !"
            )

    # 5. Agent Codeur Multimédia : Création automatique d'Ebook (PDF + EPUB + 3D + Affinity) si demandé
    if any(k in m_low for k in ["ebook", "livre", "pdf", "epub", "affinity"]):
        chaps = [
            {"titre": "Introduction & Fondations", "contenu": Synthese[:1500]},
            {"titre": "Plan d'Action Pratique", "contenu": Synthese[1500:] or Synthese},
        ]
        fichiers_ebook = creer_ebook_tous_formats(mission[:50], "Nova Infopreneur", chaps, inclure_affinity=True)
        for fmt, ch_f in fichiers_ebook.items():
            envoyer_telegram_fichier(ch_f, f"📚 Ebook ({fmt.upper()}) : {mission[:50]}")
        journal.append("📚 **Agent Codeur Multimédia** : Ebook généré en PDF A5, EPUB, Livre 3D et Pack Affinity Publisher !")

    entete_journal = "\n".join(f"- {j}" for j in journal)
    return f"### 🤖 Rapport d'exécution de tes Agents CrewAI\n{entete_journal}\n\n---\n\n{Synthese}"


# ==============================================================================
# 13. BARRE LATÉRALE (SIDEBAR) & EN-TÊTE D'ÉTAT EN DIRECT
# ==============================================================================
with st.sidebar:
    st.markdown("## 🧠 Super-Claude Studio")
    st.caption("Interface sans limite + 6 Agents CrewAI")

    moteur_choisi = st.selectbox(
        "⚡ Moteur IA actif",
        ["auto", "groq", "gemini", "openrouter", "claude_local"],
        format_func=lambda x: {
            "auto": "🔄 Auto (Anti-Blocage Groq/Gemini/OR)",
            "groq": "🚀 Groq (Llama 3.3 70B - Ultra rapide)",
            "gemini": "✨ Google Gemini 2.0 Flash",
            "openrouter": "🌐 OpenRouter (Modèles Gratuits)",
            "claude_local": "💻 Terminal PC (free-claude-code)",
        }[x],
    )

    st.divider()
    st.markdown("### 📲 Commandes Telegram en direct")
    if st.button("🔄 Lire mes réponses Telegram (/valider, /ventes...)", use_container_width=True):
        act = verifier_messages_telegram_entrants()
        if act:
            for a in act:
                st.success(a)
        else:
            st.info("Aucune nouvelle commande reçue sur Telegram.")

    cmd_manuelle = st.text_input("Simuler une commande Telegram", placeholder="/ventes ou /valider ID")
    if st.button("▶️ Exécuter la commande", use_container_width=True) and cmd_manuelle:
        st.info(executer_commande_telegram(cmd_manuelle))

    st.divider()
    st.markdown("### 📁 Fichiers produits récents")
    if st.session_state.fichiers_produits:
        for ch_p in reversed(st.session_state.fichiers_produits[-6:]):
            if os.path.exists(ch_p):
                with open(ch_p, "rb") as f_dl:
                    st.download_button(
                        f"⬇️ {os.path.basename(ch_p)}",
                        data=f_dl.read(),
                        file_name=os.path.basename(ch_p),
                        key=f"dl_side_{os.path.basename(ch_p)}",
                        use_container_width=True,
                    )
    else:
        st.caption("Aucun fichier généré dans cette session.")

    if st.button("🗑️ Effacer la conversation", use_container_width=True):
        st.session_state.studio_messages = []
        st.rerun()

# En-tête avec badges d'état
badges = []
badges.append(
    '<span class="badge-ok">🟢 IA Sans Limite Active</span>'
    if (obtenir_secret("GROQ_API_KEY") or obtenir_secret("GEMINI_API_KEY") or obtenir_secret("OPENROUTER_API_KEY"))
    else '<span class="badge-warn">⚠️ Clé IA à configurer</span>'
)
badges.append(
    '<span class="badge-ok">🟢 YouTube Connecté</span>'
    if obtenir_secret("YT_REFRESH_TOKEN")
    else '<span class="badge-warn">⚠️ YouTube Token manquant</span>'
)
badges.append(
    '<span class="badge-ok">🟢 Telegram Actif</span>'
    if obtenir_secret("TELEGRAM_BOT_TOKEN")
    else '<span class="badge-warn">⚠️ Telegram manquant</span>'
)
badges.append(
    '<span class="badge-ok">🟢 Chariow Relié</span>'
    if obtenir_secret("CHARIOW_API_KEY")
    else '<span class="badge-warn">⚠️ Chariow manquant</span>'
)

st.markdown(
    f'<div class="claude-header"><h3 style="margin:0 0 8px 0;">🧠 Super-Claude Studio & Agence Multimédia CrewAI</h3>'
    f'{"".join(badges)}</div>',
    unsafe_allow_html=True,
)


# ==============================================================================
# 14. NAVIGATION PRINCIPALE PAR ONGLETS
# ==============================================================================
tab_studio, tab_medias, tab_business, tab_marque, tab_tests = st.tabs([
    "🧠 1. Super-Claude Studio (Chat + Agents + Artefacts)",
    "🎬 2. Studio Vidéo, LTX-2 & Ebooks (PDF/EPUB/Affinity)",
    "📈 3. Chariow, YouTube & Prospection B2B",
    "📘 4. Fiche Marque & Connaissances (RAG)",
    "🧪 5. Tous les Tests & Terminal Local",
])


# ------------------------------------------------------------------------------
# ONGLET 1 : SUPER-CLAUDE STUDIO (CHAT À GAUCHE + ARTEFACTS EN DIRECT À DROITE)
# ------------------------------------------------------------------------------
with tab_studio:
    col_chat, col_art = st.columns([1.15, 0.85], gap="large")

    with col_chat:
        c1, c2, c3 = st.columns(3)
        piloter_agents = c1.checkbox("🤖 Piloter les 6 Agents CrewAI", value=True)
        activer_web = c2.checkbox("🌐 Recherche Web en direct", value=True)
        envoyer_copie_tg = c3.checkbox("📲 Copie sur Telegram", value=False)

        zone_messages = st.container(height=500)
        with zone_messages:
            for m in st.session_state.studio_messages:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])

        prompt_user = st.chat_input(
            "Écris ton ordre ici (ex: Crée une vidéo de 4 scènes sur la marche et propose-la sur YouTube)..."
        )
        if prompt_user:
            st.session_state.studio_messages.append({"role": "user", "content": prompt_user})
            with zone_messages:
                with st.chat_message("user"):
                    st.markdown(prompt_user)

                with st.chat_message("assistant"):
                    with st.spinner("🧠 Super-Claude & tes Agents CrewAI travaillent..."):
                        if piloter_agents:
                            reponse_finale = executer_mission_agents_crewai(prompt_user, moteur_ia=moteur_choisi)
                        else:
                            ctx = ""
                            if activer_web:
                                rw = chercher_web_b2b(prompt_user, max_resultats=4)
                                ctx = "RÉSULTATS WEB :\n" + "\n".join(
                                    f"- {x['titre']} ({x['url']}) : {x['extrait']}" for x in rw
                                )
                            extrait_m = chercher_dans_connaissances(prompt_user)
                            reponse_finale = appeler_modele_ia(
                                f"{ctx}\n\nFICHE MARQUE :\n{extrait_m}\n\nDEMANDE :\n{prompt_user}",
                                moteur_prefere=moteur_choisi,
                            )

                        # Si un bloc de code est généré, l'afficher dans l'Artefact à droite
                        blocs_code = re.findall(r"```(?:python|html|toml|bash)?\s*(.*?)```", reponse_finale, flags=re.S)
                        if blocs_code and st.session_state.artefact_type == "code":
                            st.session_state.artefact_contenu = blocs_code[-1].strip()
                            st.session_state.artefact_titre = "Code généré par Super-Claude"

                        if envoyer_copie_tg:
                            envoyer_telegram_texte(f"🧠 **Super-Claude** :\n{reponse_finale[:3500]}")

                        st.markdown(reponse_finale)
                        st.session_state.studio_messages.append({"role": "assistant", "content": reponse_finale})
                        st.rerun()

    with col_art:
        st.markdown(f"#### 📦 Panneau Artefact : `{st.session_state.artefact_titre}`")
        choix_vue = st.radio(
            "Affichage Artefact",
            [" Aperçu Média / Fichier", "📝 Éditeur de Code & Vérificateur", "⏳ Validations YouTube en attente"],
            horizontal=True,
            label_visibility="collapsed",
        )

        if choix_vue == " Aperçu Média / Fichier":
            ch_art = st.session_state.artefact_fichier
            if ch_art and os.path.exists(ch_art):
                ext = os.path.splitext(ch_art)[1].lower()
                if ext == ".mp4":
                    st.video(ch_art)
                elif ext in (".mp3", ".wav"):
                    st.audio(ch_art)
                elif ext == ".html":
                    with open(ch_art, "r", encoding="utf-8") as fh:
                        st.components.v1.html(fh.read(), height=420, scrolling=True)
                with open(ch_art, "rb") as f_b:
                    st.download_button(
                        f"⬇️ Télécharger {os.path.basename(ch_art)}",
                        data=f_b.read(),
                        file_name=os.path.basename(ch_art),
                        use_container_width=True,
                    )
            else:
                st.info("Lance une création (Vidéo, Ebook, Audio ou Code) pour voir l'aperçu en direct ici.")

        elif choix_vue == "📝 Éditeur de Code & Vérificateur":
            code_actuel = st.text_area(
                "Code de l'Artefact",
                value=st.session_state.artefact_contenu,
                height=380,
            )
            st.session_state.artefact_contenu = code_actuel
            c_v1, c_v2 = st.columns(2)
            if c_v1.button("🔍 Vérifier la syntaxe Python", use_container_width=True):
                try:
                    ast.parse(code_actuel)
                    st.success("✅ Syntaxe Python 100% valide !")
                except SyntaxError as e:
                    st.error(f"❌ Erreur ligne {e.lineno} : {e.msg}")
            c_v2.download_button(
                "⬇️ Télécharger .py",
                data=code_actuel.encode("utf-8"),
                file_name="artefact_code.py",
                use_container_width=True,
            )

        else:
            if not st.session_state.propositions_attente:
                st.caption("Aucune vidéo en attente de validation `/valider`.")
            for pid, pdata in list(st.session_state.propositions_attente.items()):
                st.markdown(f"**ID `{pid}`** — {pdata['titre']} (`{pdata['statut']}`)")
                b1, b2 = st.columns(2)
                if b1.button(f"✅ Publier #{pid} sur YouTube", key=f"val_{pid}"):
                    st.info(executer_commande_telegram(f"/valider {pid}"))
                if b2.button(f"🛑 Refuser #{pid}", key=f"ref_{pid}"):
                    st.warning(executer_commande_telegram(f"/refuser {pid}"))


# ------------------------------------------------------------------------------
# ONGLET 2 : STUDIO VIDÉO, LTX-2 & EBOOKS (PDF, EPUB, LIVRE 3D, AFFINITY)
# ------------------------------------------------------------------------------
with tab_medias:
    col_v, col_e = st.columns(2, gap="large")

    with col_v:
        st.subheader("🎬 Création Vidéo Short & Clip IA LTX-2")
        txt_scenes = st.text_area(
            "Scènes de la vidéo (1 scène par ligne)",
            value="Marcher 30 minutes par jour transforme ton énergie.\nCela stimule ton cœur et clarifie tes idées.\nCommence dès aujourd'hui à ton rythme.\nAbonne-toi pour plus de conseils pratiques !",
            height=130,
        )
        titre_yt = st.text_input("Titre du Short pour YouTube", value="Les bienfaits de la marche quotidienne")
        c_v1, c_v2 = st.columns(2)
        if c_v1.button("🎥 Générer le Short + Voix Off", use_container_width=True):
            with st.spinner("Montage de la vidéo et génération de la voix off..."):
                lignes_s = [l.strip() for l in txt_scenes.splitlines() if l.strip()]
                ch_v = creer_video_short(lignes_s, avec_voix=True)
                st.video(ch_v)
                st.success(f"Vidéo prête : {os.path.basename(ch_v)}")

        if c_v2.button("📲 Proposer en publication YouTube (Telegram)", use_container_width=True):
            with st.spinner("Création et envoi sur Telegram..."):
                lignes_s = [l.strip() for l in txt_scenes.splitlines() if l.strip()]
                ch_v = creer_video_short(lignes_s, avec_voix=True)
                pid = proposer_publication_telegram(ch_v, titre_yt, txt_scenes)
                st.success(f"Proposition #{pid} envoyée sur Telegram ! Réponds `/valider {pid}`.")

        st.divider()
        st.markdown("#### 🎞️ Clip vidéo IA (LTX-2 via fal.ai)")
        prompt_ltx = st.text_input(
            "Description de la scène (en anglais de préférence)",
            value="Cinematic aerial shot of a person walking on a sunny mountain trail, golden hour, 4k",
        )
        sec_ltx = st.slider("Durée du clip (secondes)", min_value=6, max_value=8, value=6)
        ok_cout = st.checkbox(f"Je confirme le coût estimé (~{sec_ltx * 0.09:.2f} $ sur mon solde fal.ai)")
        if st.button("🎞️ Générer le clip LTX-2", use_container_width=True) and ok_cout:
            with st.spinner("Génération LTX-2 en cours chez fal.ai..."):
                ok_l, res_l = generer_clip_ltx_fal(prompt_ltx, sec_ltx)
                if ok_l:
                    st.video(res_l)
                    st.success("Clip LTX-2 généré !")
                else:
                    st.error(res_l)

    with col_e:
        st.subheader("📚 Ebook : PDF A5, EPUB, Livre 3D & Pack Affinity Publisher")
        eb_titre = st.text_input("Titre du livre", value="Guide Pratique de la Marche Active")
        eb_auteur = st.text_input("Auteur", value="Nova Infopreneur")
        eb_ch1 = st.text_area(
            "Chapitre 1",
            value="La marche quotidienne est l'habitude la plus simple et la plus durable pour retrouver sa forme et sa concentration.",
            height=100,
        )
        eb_ch2 = st.text_area(
            "Chapitre 2",
            value="En pratiquant 30 minutes chaque matin, vous réduisez le stress et stimulez votre créativité entrepreneuriale.",
            height=100,
        )
        if st.button("📚 Générer PDF + EPUB + Livre 3D + Pack Affinity (.zip)", use_container_width=True):
            with st.spinner("Fabrication des 4 formats d'édition..."):
                chaps_test = [
                    {"titre": "Pourquoi marcher chaque jour", "contenu": eb_ch1},
                    {"titre": "Programme sur 4 semaines", "contenu": eb_ch2},
                ]
                res_eb = creer_ebook_tous_formats(eb_titre, eb_auteur, chaps_test, inclure_affinity=True)
                st.success("✅ Les 4 formats ont été générés !")
                for nom_f, ch_f in res_eb.items():
                    with open(ch_f, "rb") as f_dl:
                        st.download_button(
                            f"⬇️ Télécharger {nom_f.upper()} ({os.path.basename(ch_f)})",
                            data=f_dl.read(),
                            file_name=os.path.basename(ch_f),
                            key=f"eb_{nom_f}",
                            use_container_width=True,
                        )


# ------------------------------------------------------------------------------
# ONGLET 3 : CHARIOW, YOUTUBE & PROSPECTION B2B
# ------------------------------------------------------------------------------
with tab_business:
    b_col1, b_col2 = st.columns(2, gap="large")

    with b_col1:
        st.subheader("🏪 Boutique Chariow & Visibilité")
        c_ch1, c_ch2, c_ch3 = st.columns(3)
        if c_ch1.button("🏪 Tester Chariow", use_container_width=True):
            st.markdown(lire_boutique_chariow())
            st.markdown(lister_produits_chariow())
            st.markdown(resume_ventes_chariow("30j"))
        if c_ch2.button("💰 Ventes 30j", use_container_width=True):
            st.info(resume_ventes_chariow("30j"))
        if c_ch3.button("🔍 Auditer Visibilité", use_container_width=True):
            st.markdown(auditer_visibilite_boutique())

        st.divider()
        st.subheader("🎯 Prospection B2B & Carnet CSV")
        req_b2b = st.text_input("Chercher des prospects B2B sur le web", value="coach sportif entreprise france contact")
        if st.button("🔎 Trouver & Enregistrer des Prospects", use_container_width=True):
            res_p = chercher_web_b2b(req_b2b, max_resultats=4)
            for r in res_p:
                if r.get("url"):
                    enregistrer_prospect_b2b(
                        r["titre"][:60],
                        r["url"],
                        "",
                        r["extrait"][:150],
                        f"Bonjour, j'ai découvert votre activité sur {r['url']} et nos guides sur Chariow peuvent accompagner vos clients.",
                    )
            st.success(f"{len(res_p)} piste(s) ajoutée(s) au carnet !")

        if st.session_state.carnet_prospects:
            st.dataframe(st.session_state.carnet_prospects, use_container_width=True)
            ch_csv = exporter_prospects_csv()
            with open(ch_csv, "rb") as fc:
                st.download_button("⬇️ Exporter le Carnet CSV (Excel)", data=fc.read(), file_name="prospects_b2b.csv")

    with b_col2:
        st.subheader("📺 YouTube : Hashtags SEO & Commentaires")
        sujet_tag = st.text_input("Sujet pour trouver des hashtags YouTube", value="marche rapide santé")
        if st.button("🏷️ Voir les hashtags proposés", use_container_width=True):
            tags_s = suggerer_hashtags_youtube(sujet_tag)
            st.code(" ".join(tags_s))

        st.divider()
        st.markdown("#### 💬 Commentaires YouTube sans réponse")
        if st.button("💬 Voir les commentaires sans réponse", use_container_width=True):
            ok_c, liste_c = lister_commentaires_sans_reponse()
            if not ok_c:
                st.error(liste_c[0].get("erreur"))
            elif not liste_c:
                st.success("✅ Aucun commentaire en attente de réponse !")
            else:
                for c in liste_c:
                    st.markdown(f"- **{c['auteur']}** : « {c['texte']} »")
                st.info("Tape `/commentaires` dans la barre de gauche ou sur Telegram pour générer les brouillons.")


# ------------------------------------------------------------------------------
# ONGLET 4 : FICHE MARQUE & CONNAISSANCES (RAG)
# ------------------------------------------------------------------------------
with tab_marque:
    st.subheader("📘 Fiche Marque & Base de Connaissances de tes Agents")
    st.caption("Tous tes agents lisent cette fiche avant d'écrire un post, un ebook ou une réponse client.")

    nv_fiche = st.text_area(
        "Contenu de ta Fiche Marque (Markdown)",
        value=st.session_state.fiche_marque_texte,
        height=260,
    )
    c_fm1, c_fm2 = st.columns(2)
    if c_fm1.button("💾 Enregistrer et utiliser cette Fiche Marque", use_container_width=True):
        st.session_state.fiche_marque_texte = nv_fiche
        try:
            with open("fiche_marque.md", "w", encoding="utf-8") as f:
                f.write(nv_fiche)
        except Exception:
            pass
        st.success("✅ Fiche Marque mise à jour pour tous les agents !")

    c_fm2.download_button(
        "⬇️ Télécharger fiche_marque.md",
        data=nv_fiche.encode("utf-8"),
        file_name="fiche_marque.md",
        use_container_width=True,
    )

    fichiers_up = st.file_uploader("Ajouter des documents (.txt, .md)", type=["txt", "md"], accept_multiple_files=True)
    for fu in fichiers_up or []:
        st.session_state.docs_connaissances[fu.name] = fu.read().decode("utf-8", errors="replace")
        st.success(f"Document ajouté : {fu.name}")

    q_test = st.text_input("Tester une question sur ta base de connaissances", value="Comment je reçois le produit ?")
    if st.button("🔎 Tester la recherche dans la base"):
        st.info(chercher_dans_connaissances(q_test))


# ------------------------------------------------------------------------------
# ONGLET 5 : TOUS LES BOUTONS DE TEST & TERMINAL LOCAL (FREE-CLAUDE-CODE)
# ------------------------------------------------------------------------------
with tab_tests:
    st.subheader("🧪 Tableau de Test Rapide (dans l'ordre)")
    t1, t2, t3 = st.columns(3)

    with t1:
        if st.button("1️⃣ Tester Telegram", use_container_width=True):
            ok, msg = envoyer_telegram_texte("✅ Test Telegram réussi depuis ton Super-Claude Studio !")
            st.success(msg) if ok else st.error(msg)

        if st.button("2️⃣ Tester la voix", use_container_width=True):
            ch_a = generer_voix_off("Bonjour patron, la synthèse vocale de ton agence fonctionne parfaitement.")
            st.audio(ch_a)

    with t2:
        if st.button("3️⃣ Tester vidéo + livre + voix off", use_container_width=True):
            with st.spinner("Création vidéo + livre 3D..."):
                ch_v = creer_video_short(["Scène 1 : Test vidéo", "Scène 2 : Voix off active"], avec_voix=True)
                ch_l = generer_livre_3d_html("Livre Test", "Nova", [{"titre": "Chapitre 1", "contenu": "Contenu de test."}])
                st.video(ch_v)
                st.success(f"Vidéo ({os.path.basename(ch_v)}) et Livre 3D ({os.path.basename(ch_l)}) créés !")

        if st.button("4️⃣ Tester PDF + EPUB + Pack Affinity", use_container_width=True):
            res = creer_ebook_tous_formats(
                "Ebook de Test", "Nova", [{"titre": "Chapitre 1", "contenu": "Texte de vérification."}], inclure_affinity=True
            )
            st.success("PDF, EPUB, Livre 3D et Pack Affinity générés dans la barre de gauche !")

    with t3:
        if st.button("5️⃣ Tester l'envoi YouTube (privé)", use_container_width=True):
            with st.spinner("Création d'une mini vidéo et envoi privé sur YouTube..."):
                ch_test = creer_video_short(["Vidéo de test (à supprimer)"], avec_voix=False, prefixe="test_yt")
                ok_y, msg_y = publier_video_youtube(
                    ch_test, "Test agence (à supprimer)", "Vidéo de test", ["#Shorts"], forcer_prive=True
                )
                st.success(msg_y) if ok_y else st.error(msg_y)

        if st.button("6️⃣ Tester la recherche web", use_container_width=True):
            st.json(chercher_web_b2b("formation entrepreneuriat en ligne", max_resultats=3))

    st.divider()
    st.subheader("💻 Pont avec ton installation locale (`free-claude-code`)")
    st.markdown(
        "Si tu lances ce fichier sur ton PC (`py -m streamlit run app.py`), "
        "le mode **💻 Terminal PC (free-claude-code)** dans le menu de gauche utilise directement ton installation locale !"
    )
