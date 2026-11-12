"""List which of the model codes this job uses are in the account's model list (no tokens spent)."""
import ali

ids = ali.list_models()
want = ["kimi-k3", "deepseek-v4-pro-0813", "qwen3.8-max-0902", "kimi-k3.1-pro", "glm-5.3", "deepseek-v4.1-flash-1017",
        "qwen3.8-flash", "deepseek-v4.1-flash", "qwen3.8-27b"]
print(len(ids), "models listed")
for w in want:
    print(w, "listed" if w in ids else "NOT LISTED")
print("same-family listed codes:", [i for i in ids if any(f in i for f in ("kimi", "glm", "deepseek"))][:30])
