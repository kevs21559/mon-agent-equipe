import streamlit as st
import os
import asyncio
import threading
import requests
from crewai import Agent, Task, Crew, Process
from crewai.llm import LLM
from crewai.tools import tool
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION DES SECRETS ---
os.environ["GROQ_API_KEY"] = st.secrets.get("GROQ_API_KEY", "")
os.environ["GEMINI_API_KEY"] = st.secrets.get("GEMINI_API_KEY", "")
os.environ["OPENROUTER_API_KEY"] = st.secrets.get("OPENROUTER_API_KEY", "")
TELEGRAM_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")

# --- INITIALISATION DES MODÈLES LITELLM ---
def get_llm(model_name="cerveau-groq", temp=0.2):
    api_key = ""
    if "groq" in model_name:
        api_key = os.environ.get("GROQ_API_KEY")
    elif "gemini" in model_name:
        api_key = os.environ.get("GEMINI_API_KEY")
    elif "openrouter" in model_name:
        api_key = os.environ.get("OPENROUTER_API_KEY")

    # Nettoyage du nom pour LiteLLM (ex: "groq/llama3" au lieu de "cerveau-groq")
    model_mapping = {
        "cerveau-groq": "groq/llama3-8b-8192",
        "cerveau-gemini": "gemini/gemini-1.5-flash",
        "cerveau-openrouter": "openrouter/meta-llama/llama-3-8b-instruct:free"
    }
    
    return LLM(
        model=model_mapping.get(model_name, model_name),
        temperature=temp,
        api_key=api_key
    )

# --- FONCTION POUR ENVOYER UN MESSAGE SUR TELEGRAM ---
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Erreur Telegram: {e}")

# --- OUTIL DE DEMANDE D'OUTIL MANQUANT ---
@tool("Demander un outil manquant")
def demander_outil_manquant(outil: str) -> str:
    """Envoie un message sur Telegram pour demander une clé API ou un outil manquant au patron."""
    msg = f"🚨 ALERTE AGENT : J'ai besoin de l'outil/clé suivant : {outil}. Ajoute-le dans les secrets Streamlit !"
    send_telegram(msg)
    return "Message envoyé au patron."

# --- DEFINITION DES 5 AGENTS ---
REGLE_STRICTE = "Si tu ne sais pas ou si tu n'as pas l'outil, dis-le clairement. N'invente JAMAIS de fausses données."

responsable = Agent(
    role="Responsable d'agence",
    goal="Découper le travail, vérifier les outils, et demander l'accord du patron avant publication.",
    backstory=f"Tu es le chef. Tu ne fais pas le travail, tu coordonnes. {REGLE_STRICTE}",
    llm=get_llm("cerveau-gemini", 0.1),
    verbose=True,
    allow_delegation=True
)

chercheur = Agent(
    role="Chercheur",
    goal="Trouver de vrais clients et analyser les tendances sans jamais inventer.",
    backstory=f"Tu es un chasseur de clients. Tu cherches sur le web. {REGLE_STRICTE}",
    llm=get_llm("cerveau-groq", 0.1),
    verbose=True,
    tools=[demander_outil_manquant]
)

marketeur = Agent(
    role="Marketeur",
    goal="Rédiger des textes, e-books et scénarios vidéo convaincants.",
    backstory=f"Tu es un copywriter hors pair. Tu écris pour vendre. {REGLE_STRICTE}",
    llm=get_llm("cerveau-groq", 0.2),
    verbose=True
)

codeur = Agent(
    role="Codeur",
    goal="Créer des logiciels, des e-books au format PDF/EPUB/Flipbook et des vidéos animées.",
    backstory=f"Tu es un développeur expert en Python, MoviePy et HTML5. {REGLE_STRICTE}",
    llm=get_llm("cerveau-gemini", 0.1),
    verbose=True,
    tools=[demander_outil_manquant]
)

community_manager = Agent(
    role="Community Manager",
    goal="Créer des posts et publier sur les réseaux sociaux.",
    backstory=f"Tu gères les réseaux. Tu attends l'ordre du chef. {REGLE_STRICTE}",
    llm=get_llm("cerveau-openrouter", 0.2),
    verbose=True,
    tools=[demander_outil_manquant]
)

# --- FONCTION PRINCIPALE POUR LANCER LA CREW ---
def lancer_mission(ordre_du_patron):
    tache_recherche = Task(
        description=f"Cherche des informations pour : {ordre_du_patron}",
        agent=chercheur,
        expected_output="Une liste d'informations vérifiées."
    )
    
    crew = Crew(
        agents=[responsable, chercheur, marketeur, codeur, community_manager],
        tasks=[tache_recherche],
        process=Process.hierarchical,
        manager_agent=responsable, # <-- Utilisation de manager_agent
        max_rpm=10
    )
    return crew.kickoff()

# --- INTERFACE STREAMLIT ---
st.title("🤖 Mon Agence d'Agents IA")
st.write("Bienvenue patron. Entrez votre ordre ci-dessous :")

ordre = st.text_input("Quel est votre ordre ? (ex: Trouve 3 clients pour un e-book sur le fitness)")

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

# --- BOT TELEGRAM EN ARRIÈRE-PLAN ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bonjour patron ! Je suis prêt. Envoyez-moi un ordre.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ordre = update.message.text
    await update.message.reply_text(f"Ordre reçu : {ordre}. Je lance l'équipe.")
    try:
        resultat = lancer_mission(ordre)
        await update.message.reply_text(f"Résultat : {resultat}")
    except Exception as e:
        await update.message.reply_text(f"Erreur : {e}")

def run_telegram_bot():
    if TELEGRAM_TOKEN:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_telegram_bot, daemon=True).start()
