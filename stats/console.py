"""The console page: one screen, server-rendered, no script, no external asset."""

import json
from datetime import UTC, datetime
from html import escape

LABELS = {
    "app_opened": "Ouvertures de l'app",
    "ringtone_created": "Sonneries créées",
    "ringtone_installed": "Sonneries installées",
    "ad_watched": "Pubs regardées",
    "paywall_shown": "Paywall affiché",
    "purchase": "Achats",
    "auto_best_part_opened": "Meilleur passage : bouton touché",
    "auto_best_part_started": "Meilleur passage : recherche lancée",
    "auto_best_part_completed": "Meilleur passage : recherche finie",
    "auto_best_part_option_selected": "Meilleur passage : proposition choisie",
    "smart_audio_enhance_opened": "Améliorer le son : bouton touché",
    "smart_audio_enhance_applied": "Améliorer le son : allumé",
    "smart_audio_enhance_undone": "Améliorer le son : éteint",
    "smart_audio_enhance_compared": "Améliorer le son : comparaison",
    "speed_sampled": "Vitesse : aperçu de 5 s écouté",
    "app_color_chosen": "Couleur de l'app choisie",
    "app_style_chosen": "Style de l'app choisi (Classique / Pop)",
    "sound_favorited": "Son intégré mis en favori (ou retiré)",
    "contact_assignment_started": "Contact : écran ouvert",
    "contact_permission_granted": "Contact : accès accepté",
    "contact_permission_denied": "Contact : accès refusé",
    "contact_selected": "Contact : choisi",
    "paywall_plan_selected": "Offre : formule touchée",
    "paywall_expanded": "Offre : avantages dépliés",
    "purchase_started": "Achat : lancé",
    "purchase_ended": "Achat : terminé",
    "paywall_dismissed": "Offre : quittée",
    "onboarding_step": "Bienvenue : écran atteint",
    "onboarding_effect": "Bienvenue : effet essayé",
    "contact_install_tapped": "Contact : installation lancée",
    "contact_assignment_completed": "Contact : fiche ouverte",
    "screen_viewed": "Écran vu",
    "crash_reported": "Plantage (signalé au lancement suivant)",
}

STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--sub:#6b7280;--line:#e6e8ec;--accent:#0bb5c7}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--ink:#eef0f3;--sub:#9aa1ad;--line:#2a2e36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:26px;margin:0}h2{font-size:17px;margin:0 0 12px}
.sub{color:var(--sub)}.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.big{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}
section{margin-top:20px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left}td.n{text-align:right}
.cols{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
svg text{fill:var(--sub);font-size:10px}a{color:var(--accent)}
"""


# How ringtone_created details read in the console. An unknown one shows as sent.
EFFECT_LABELS = {
    "source": "Source", "best_part": "Meilleur passage", "enhance": "Son amélioré",
    "speed": "Vitesse", "loop": "Boucle parfaite", "premium": "Créée en Premium",
    "fade_in": "Fondu de début (s)", "fade_out": "Fondu de fin (s)", "volume": "Volume (%)",
    "bass": "Bass boost", "optimized": "Niveau optimisé", "sound": "Son intégré",
    "duration": "Durée (s)", "quick": "Son en 2 touches (sans l’éditeur)",
}
VALUE_LABELS = {
    "yes": "oui", "no": "non", "video": "vidéo", "audio": "audio", "sound": "son intégré",
    "normal": "normale", "sped_up": "accélérée", "slowed": "ralentie",
    "off": "non", "light": "léger", "strong": "fort",
}


def effect_rows(effects: dict) -> list[tuple]:
    total = effects["creations"]
    return [(EFFECT_LABELS.get(r["detail"], r["detail"]), VALUE_LABELS.get(r["value"], r["value"]),
             r["count"], f"{round(100 * r['share'])} %") for r in effects["rows"]] if total else []


def pct(pair: tuple[int, int]) -> str:
    back, total = pair
    return "—" if total == 0 else f"{round(100 * back / total)} %"


def table(rows, headers) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="n">{escape(str(c))}</td>' if isinstance(c, (int, float))
                          else f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body or '<tr><td class=sub>Rien pour l’instant</td></tr>'}</table>"


def chart(daily: list[dict]) -> str:
    peak = max([d["active"] for d in daily] + [d["created"] for d in daily] + [1])
    w, h, gap = 1000, 140, 4
    bw = w / len(daily) - gap
    bars = []
    for i, d in enumerate(daily):
        x = i * (bw + gap)
        ha = d["active"] / peak * h
        hc = d["created"] / peak * h
        bars.append(f'<rect x="{x:.1f}" y="{h - ha:.1f}" width="{bw:.1f}" height="{ha:.1f}" rx="2" '
                    f'fill="var(--line)"><title>{d["active"]} actifs</title></rect>')
        bars.append(f'<rect x="{x + bw * .25:.1f}" y="{h - hc:.1f}" width="{bw * .5:.1f}" height="{hc:.1f}" rx="2" '
                    f'fill="var(--accent)"><title>{d["created"]} sonneries</title></rect>')
        if i % 7 == 0:
            day = datetime.fromtimestamp(d["day"], UTC).strftime("%d/%m")
            bars.append(f'<text x="{x:.1f}" y="{h + 14}">{day}</text>')
    return (f'<svg viewBox="0 0 {w} {h + 18}" width="100%" role="img" '
            f'aria-label="30 derniers jours">{"".join(bars)}</svg>')


def render(m: dict, include_tests: bool) -> str:
    r = m["retention"]
    cards = [
        ("Appareils", m["devices"]), ("Actifs 24 h", m["active_1"]), ("Actifs 7 j", m["active_7"]),
        ("Actifs 30 j", m["active_30"]), ("Nouveaux 7 j", m["new_7"]),
        ("Sonneries créées", m["created_total"]), ("Créées 7 j", m["created_7"]),
        ("Par créateur (moy.)", m["per_creator"]),
    ]
    cards_html = "".join(f'<div class="card"><div class="sub">{escape(k)}</div><div class="big">{v}</div></div>'
                         for k, v in cards)
    ret = "".join(
        f'<div class="card"><div class="sub">Revenus après {n} j</div><div class="big">{pct(r[n])}</div>'
        f'<div class="sub">{r[n][0]} sur {r[n][1]} appareils assez anciens</div></div>' for n in (1, 7, 30))
    features = [(LABELS.get(n, n), c, d) for n, c, d in m["features"]]
    details = [(LABELS.get(n, n), ", ".join(f"{k} = {v}" for k, v in json.loads(p).items()), c)
               for n, p, c in m["details"]]
    toggle = ('<a href="?tests=0">Masquer les appareils de test</a>' if include_tests
              else f'<a href="?tests=1">Inclure les appareils de test ({m["tests_hidden"]} masqués)</a>')
    updated = datetime.fromtimestamp(m["now"], UTC).strftime("%d/%m/%Y %H:%M UTC")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Phone Ringtones · Stats</title>
<style>{STYLE}</style></head><body><main>
<h1>Phone Ringtones</h1><p class="sub">Mis à jour {updated} · {toggle}</p>
<section class="grid">{cards_html}</section>
<section><h2>Qui revient</h2><div class="grid">{ret}
<div class="card"><div class="sub">Revenus au moins une fois</div><div class="big">{m["came_back"]}</div>
<div class="sub">appareils ouverts un autre jour que le premier</div></div></div></section>
<section class="card"><h2>30 derniers jours</h2><p class="sub">Gris : appareils actifs · couleur : sonneries créées</p>
{chart(m["daily"])}</section>
<section class="cols">
<div class="card"><h2>Sonneries par appareil</h2>{table(m["buckets"], ["Sonneries", "Appareils"])}</div>
<div class="card"><h2>Fonctions, 30 jours</h2>{table(features, ["Événement", "Fois", "Appareils"])}</div>
</section>
<section class="card"><h2>Effets utilisés, 30 jours</h2>
<p class="sub">Part des {m["effects"]["creations"]} sonneries créées ces 30 jours.</p>
{table(effect_rows(m["effects"]), ["Réglage", "Valeur", "Sonneries", "Part"])}</section>
<section class="card"><h2>Détails, 30 jours</h2>{table(details, ["Événement", "Valeur", "Fois"])}</section>
<section class="cols">
<div class="card"><h2>Versions d'iOS</h2>{table(m["os"], ["iOS", "Appareils"])}</div>
<div class="card"><h2>Pays</h2>{table(m["country"], ["Pays", "Appareils"])}</div>
<div class="card"><h2>Langues</h2>{table(m["lang"], ["Langue", "Appareils"])}</div>
<div class="card"><h2>Versions de l'app</h2>{table(m["app"], ["Version", "Appareils"])}</div>
<div class="card"><h2>Modèles</h2>{table(m["model"], ["Modèle", "Appareils"])}</div>
<div class="card"><h2>Événements refusés</h2><p class="sub">Inconnus du serveur : comptés, jamais gardés.</p>
{table(m["dropped"], ["Nom", "Fois"])}</div>
</section></main></body></html>"""
