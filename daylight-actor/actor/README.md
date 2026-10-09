# Daylight Actor

Node.js Apify Actor (ESM, `apify` SDK v3). Paste `src/main.js`, `package.json`, `Dockerfile`,
`.actor/actor.json` and `.actor/input_schema.json` into a new Actor, then Build.

Environment variables (Actor, Source tab, Environment variables):

| Name | Required | Meaning |
|---|---|---|
| `LINKEDIN_ACTOR` | no | e.g. `harvestapi~linkedin-profile-scraper` |
| `LINKEDIN_INPUT` | no | JSON template with `{{URL}}` |
| `OPENAI_API_KEY` | no | use OpenAI directly instead of the Apify proxy |
| `LLM_MODEL` | no | model name override |
