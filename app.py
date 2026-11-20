import os
import asyncio
import threading
import requests
import streamlit as st

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
        return st.secrets.get(nom, defaut)
    except Exception:
        return defaut


os.environ["GROQ_API_KEY"] = secret("GROQ_API_KEY")
os.environ["GEMINI_API_KEY"] = secret("GEMINI_API_KEY")
os.environ["OPENROUTER_API_KEY"] = secret("OPENROUTER_API_KEY")
TELEGRAM_TOKEN = secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = secret("TELEGRAM_CHAT_ID")

# Noms de modèles modifiables depuis les secrets Streamlit (sans toucher au code)
MODELES = {
    "groq": secret("MODEL_GROQ", "groq/llama-3.1-8b-instant"),
    "gemini": secret("MODEL_GEMINI", "gemini/gemini-2.5-flash"),
    "openrouter": secret(
        "MODEL_OPENROUTER", "openrouter/meta-llama/llama-3.1-8b-instruct:free"
    ),
}


# =====================================================
# INITIALISATION DES MODÈLES
# =====================================================
def get_llm(fournisseur="groq", temp=0.2):
    cles = {
        "groq": os.environ.get("GROQ_API_KEY"),
        "gemini": os.environ.get("GEMINI_API_KEY"),
        "openrouter": os.environ.get("OPENROUTER_API_KEY"),
    }
    return LLM(
        model=MODELES[fournisseur],
        temperature=temp,
        api_key=cles[fournisseur],
    )


# =====================================================
# ENVOI D'UN MESSAGE TELEGRAM
# =====================================================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Erreur Telegram: {e}")


# =====================================================
# OUTIL : DEMANDER UN OUTIL MANQUANT
# =====================================================
@tool("Demander un outil manquant")
def demander_outil_manquant(outil: str) -> str:
    """Envoie un message sur Telegram pour demander une clé API ou un outil manquant au patron."""
    msg = (
        f"🚨 ALERTE AGENT : J'ai besoin de l'outil/clé suivant : {outil}. "
        "Ajoute-le dans les secrets Streamlit !"
    )
    send_telegram(msg)
    return "Message envoyé au patron."


# =====================================================
# DÉFINITION DES 5 AGENTS
# =====================================================
REGLE_STRICTE = (
    "Si tu ne sais pas ou si tu n'as pas l'outil, dis-le clairement. "
    "N'invente JAMAIS de fausses données."
)


def creer_agents():
    # Le manager n'a PAS d'outils (obligatoire en mode hiérarchique)
    responsable = Agent(
        role="Responsable d'agence",
        goal="Découper le travail, vérifier les outils, et demander l'accord du patron avant publication.",
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
        goal="Rédiger des textes, e-books et scénarios vidéo convaincants.",
        backstory=f"Tu es un copywriter hors pair. Tu écris pour vendre. {REGLE_STRICTE}",
        llm=get_llm("groq", 0.2),
        verbose=True,
    )

    codeur = Agent(
        role="Codeur",
        goal="Créer des logiciels, des e-books au format PDF/EPUB/Flipbook et des vidéos animées.",
        backstory=f"Tu es un développeur expert en Python, MoviePy et HTML5. {REGLE_STRICTE}",
        llm=get_llm("gemini", 0.1),
        verbose=True,
        tools=[demander_outil_manquant],
    )

    community_manager = Agent(
        role="Community Manager",
        goal="Créer des posts pour les réseaux sociaux.",
        backstory=f"Tu gères les réseaux. Tu attends l'ordre du chef. {REGLE_STRICTE}",
        llm=get_llm("openrouter", 0.2),
        verbose=True,
        tools=[demander_outil_manquant],
    )

    return responsable, chercheur, marketeur, codeur, community_manager


# =====================================================
# LANCEMENT D'UNE MISSION
# =====================================================
def lancer_mission(ordre_du_patron):
    responsable, chercheur, marketeur, codeur, community_manager = creer_agents()

    # En mode hiérarchique, le manager attribue la tâche : pas d'agent fixé
    tache = Task(
        description=f"Réalise la mission suivante demandée par le patron : {ordre_du_patron}",
        expected_output="Un résultat clair, structuré, basé uniquement sur des informations vérifiables.",
    )

    crew = Crew(
        agents=[chercheur, marketeur, codeur, community_manager],  # sans le manager
        tasks=[tache],
        process=Process.hierarchical,
        manager_agent=responsable,
        max_rpm=10,
    )
    return str(crew.kickoff())


# =====================================================
# BOT TELEGRAM (démarré une seule fois)
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bonjour patron ! Je suis prêt. Envoyez-moi un ordre.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ordre_recu = update.message.text
    await update.message.reply_text(f"Ordre reçu : {ordre_recu}. Je lance l'équipe.")
    try:
        resultat = await asyncio.to_thread(lancer_mission, ordre_recu)
        # Telegram limite les messages à 4096 caractères
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
        bot_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        )
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
    "Quel est votre ordre ? (ex: Trouve 3 clients pour un e-book sur le fitness)"
)

if st.button("Lancer la mission"):
    if ordre:
        with st.spinner("Les agents travaillent..."):
            try:
                resultat = lancer_mission(ordre)
                st.success("Mission terminée !")
                st.write(resultat)
            except Exception as e:
                st.error(f"Erreur : {e}")
    else:
        st.warning("Veuillez écrire un ordre.")
