# Architechture CORE

core/
├── app.py                 → entrypoint minimal
├── gateway/              → entrées (HTTP / WS / Telegram)
├── control_plane/        → orchestration globale (NEW)
├── pipeline/             → cerveau décisionnel
├── agents/               → exécution
├── memory/               → état long terme
├── llm_client/           → abstraction LLM
├── modules/              → services (skills, scheduler)
