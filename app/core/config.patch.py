# ============================================================
# PATCH config — à ajouter dans app/core/config.py
# ============================================================
# Ajoutez ces lignes DANS la classe `Settings` (à côté des autres
# groupes de paramètres — par ex. après la section SMS_WEBHOOK).
# Puis complétez votre .env avec les variables listées plus bas.

# --- IA (fournisseur compatible OpenAI : OpenAI, Lovable AI Gateway,
#         Groq, Mistral, Together, Ollama...) ---
IA_BASE_URL: str = "https://api.openai.com/v1"
IA_API_KEY: str | None = None
IA_MODEL: str = "gpt-4o-mini"
IA_TIMEOUT_SECONDS: int = 60


# ============================================================
# .env — exemples
# ============================================================
#
# # OpenAI (par défaut) :
# IA_BASE_URL=https://api.openai.com/v1
# IA_API_KEY=sk-...
# IA_MODEL=gpt-4o-mini
#
# # Lovable AI Gateway :
# IA_BASE_URL=https://ai.gateway.lovable.dev/v1
# IA_API_KEY=<LOVABLE_API_KEY>
# IA_MODEL=google/gemini-2.5-flash
#
# # Groq (gratuit / rapide) :
# IA_BASE_URL=https://api.groq.com/openai/v1
# IA_API_KEY=gsk_...
# IA_MODEL=llama-3.3-70b-versatile
#
# # Mistral :
# IA_BASE_URL=https://api.mistral.ai/v1
# IA_API_KEY=...
# IA_MODEL=mistral-small-latest
#
# # Ollama local (aucun coût, aucune donnée sortante) :
# IA_BASE_URL=http://localhost:11434/v1
# IA_API_KEY=ollama
# IA_MODEL=llama3.1:8b
