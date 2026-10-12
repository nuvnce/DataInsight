"""
context_processors.py — variables disponibles dans tous les templates.

`active` : identifie l'entrée de menu courante (dashboard / imports / cartes /
stats / admin) à partir du chemin de l'URL, pour surligner la barre latérale
sans que chaque vue ait à passer la variable.
"""


def nav_active(request):
    path = request.path
    if path.startswith("/collecte"):
        active = "imports"
    elif path.startswith("/admin"):
        active = "admin"
    elif path.startswith("/cartes"):
        active = "cartes"
    elif path.startswith("/stats"):
        active = "stats"
    elif path == "/" or path.startswith("/dashboard"):
        active = "dashboard"
    else:
        active = ""
    return {"active": active}
