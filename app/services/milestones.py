"""Versioned, server-owned milestone definitions and projections."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

VERSION = 2
# Historical definitions are display-only; never active reward offers.
LEGACY_ACHIEVEMENTS = {'first': ('Primeira descoberta', 'Fotografe seu primeiro gato em um passeio ao ar livre.', 1),
 'collector_5': ('Olhar Curioso', 'Aviste e catalogue 5 gatos diferentes pelo seu bairro.', 5),
 'collector_10': ('Rastreador de Pegadas', 'Catalogue 10 gatos diferentes em sua jornada.', 10),
 'master_collector': ('Mestre do Gatálogo', 'Reúna 20 gatos catalogados na sua coleção.', 20),
 'legend_collector': ('Enciclopédia Felina', 'Alcance a incrível marca de 50 gatos catalogados.', 50),
 'black_cat': ('Pantera Urbana', 'Catalogue um gato com pelagem predominantemente preta.', 1),
 'orange_tabby': ('Sol Alaranjado', 'Catalogue um gato com pelagem ruiva ou laranja.', 1),
 'calico': ('Mosaico da Sorte', 'Catalogue um gato com pelagem tricolor ou cálico.', 1),
 'siamese_pattern': ('Olhar Royal', 'Catalogue um gato com padrão siamês ou colorpoint.', 1),
 'bicolor': ('Frajola Elegante', 'Catalogue um gato bicolor ou frajola.', 1),
 'neighborhood_3': ('Explorador de Quarteirões', 'Registre gatos em pelo menos 3 locais diferentes.', 3),
 'city_wanderer': ('Andarilho Urbano', 'Registre gatos em 5 locais diferentes.', 5),
 'night_owl': ('Observador Noturno', 'Catalogue um gato no período noturno (após 19h).', 1),
 'early_bird': ('Madrugador', 'Catalogue um gato nas primeiras horas da manhã (antes das 8h).', 1),
 'days': ('Olhar Atento', 'Registre descobertas em três dias diferentes.', 3),
 'streak_3': ('Ritmo Felino', 'Mantenha uma sequência de 3 dias de atividade.', 3),
 'streak_7': ('Semana Guardiã', 'Mantenha uma sequência de 7 dias de atividade.', 7),
 'streak_30': ('Devoção Felina', 'Mantenha uma sequência lendária de 30 dias de atividade.', 30),
 'angles': ('Um Novo Olhar', 'Adicione fotos complementares a um gato.', 2),
 'portrait': ('Retrato Detalhado', 'Adicione notas descritivas detalhadas a 3 gatos.', 3),
 'angles_master': ('Galeria Completa', 'Adicione 4 ou mais fotos ao perfil de um mesmo gato.', 4),
 'helper': ('Rede de Cuidado', 'Contribua com um avistamento ou pista de um gato desaparecido.', 1),
 'guardian_shield': ('Vigilante do Bairro', 'Verifique casos de busca ativa para apoiar tutores.', 1),
 'poster_share': ('Voz da Esperança', 'Compartilhe um cartaz de busca para mobilizar a comunidade.', 1),
 'social_heart': ('Coração Comunitário', 'Curta 5 publicações no feed da comunidade.', 5),
 'commentator': ('Conversa Afiada', 'Participe com comentários em 3 publicações.', 3),
 'first_share': ('Espalhando Carinho', 'Compartilhe uma descoberta felina com amigos.', 1),
 'owned_pet': ('Meu Companheiro', 'Cadastre um gato sob seus cuidados como pet próprio.', 1)}
CALENDAR = ZoneInfo("America/Sao_Paulo")
# Stable legacy IDs preserve previous one-time grants.
ACHIEVEMENTS = (
    ("first", "Primeira descoberta", "Confirme seu primeiro gato diferente.", "individuals", 1, 100, 50, 10),
    ("collector_5", "Olhar curioso", "Conheça cinco gatos diferentes.", "individuals", 5, 300, 150, 25),
    ("collector_10", "Explorador felino", "Conheça dez gatos diferentes.", "individuals", 10, 650, 300, 50),
    ("master_collector", "Mestre do Gatálogo", "Conheça vinte gatos diferentes.", "individuals", 20, 1500, 600, 100),
    ("legend_collector", "Enciclopédia felina", "Conheça cinquenta gatos diferentes.", "individuals", 50, 3000, 1200, 200),
    ("days", "Histórias em três dias", "Guarde fotos válidas em três dias distintos.", "photo_days", 3, 150, 75, 15),
    ("colors", "Cores do álbum", "Confirme três cores diferentes nas fichas.", "colors", 3, 200, 100, 20),
    ("angles", "Um novo olhar", "Acrescente dois complementos úteis ao álbum.", "complements", 2, 150, 75, 15),
    ("album_10", "Álbum vivo", "Acrescente dez complementos úteis.", "complements", 10, 800, 350, 60),
    ("care_10", "Laços de cuidado", "Cuide do quintal em dez dias diferentes.", "care_days", 10, 500, 250, 40),
    ("helper", "Rede de cuidado", "Tenha uma pista reconhecida por um tutor.", "clues", 1, 300, 150, 30),
    ("garden_50", "Colheitas com carinho", "Complete cinquenta colheitas da horta.", "quintal_harvest", 50, 400, 200, 30),
    ("favorite_recipe", "O sabor favorito", "Descubra a primeira receita favorita de um gato.", "quintal_favorite", 1, 500, 250, 40),
    ("quintal_spaces", "Um quintal cheio de vida", "Conclua cinco construções ou ampliações.", "quintal_facility", 5, 400, 200, 30),
    ("quintal_bond", "Uma amizade para sempre", "Alcance duzentos pontos de vínculo com um gato.", "quintal_bond", 200, 800, 400, 60),
)


def calendar(now=None):
    local = (now or datetime.now(UTC)).astimezone(CALENDAR)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    return {"timezone": str(CALENDAR), "server_now": local.isoformat(),
            "day": today.date().isoformat(), "week": monday.date().isoformat(),
            "daily_reset_at": (today + timedelta(days=1)).isoformat(),
            "weekly_reset_at": (monday + timedelta(days=7)).isoformat()}


def valid_events(events):
    return [e for e in events if ((e.evidence or {}).get("eligible") or (e.evidence or {}).get("activity_valid")) and e.kind in
            {"discovery", "complement", "care", "post", "comment", "repost", "clue", "review", "encounter", "quintal"}]


def counters(events):
    valid = valid_events(events)
    photos = [e for e in valid if e.kind in {"discovery", "complement"}]
    identities = {e.evidence.get("individual_id") or e.record_id for e in valid if e.kind == "discovery"}
    colors = {e.evidence.get("attributes", {}).get("primary_color") for e in photos
              if e.evidence.get("attributes_confirmed")}
    colors.discard(None)
    production = [e for e in valid if e.kind == "quintal"]
    activity = [e for e in production if e.evidence.get("metric") in {"quintal_meal", "quintal_water_cat", "quintal_play"}]
    care_days = [e for e in production if e.evidence.get("metric") == "quintal_care_day"]
    totals = {}
    for event in production:
        metric = event.evidence.get("metric")
        if metric == "quintal_bond": totals[metric] = max(totals.get(metric, 0), int(event.evidence.get("value", 0)))
        elif metric: totals[metric] = totals.get(metric, 0) + int(event.evidence.get("quantity", 1))
    return {**totals, "individuals": len(identities), "photos": len(photos),
            "complements": sum(e.kind == "complement" for e in valid),
            "photo_days": len({e.evidence.get("local_day") for e in photos}),
            "care": sum(e.kind == "care" for e in valid) + sum(int(e.evidence.get("quantity", 1)) for e in activity),
            "care_days": len({e.evidence.get("local_day") for e in [*[e for e in valid if e.kind == "care"], *activity, *care_days] if e.evidence.get("local_day")}),
            "clues": sum(e.kind == "clue" for e in valid),
            "community": sum(e.kind in {"post", "repost", "comment"} for e in valid),
            "reviews": sum(e.kind == "review" for e in valid)}


# Small, optional alternatives. Reuse confirmed activity dates, not login time.
# Collection-only choices never require a new crop or another session that day.
ROUTINE_OPTIONS = (
    ("cats", "care", "Fazer carinho em um gato", "start_care"),
    ("garden", "quintal_harvest", "Colher um canteiro pronto", "start_harvest"),
    ("kitchen", "quintal_cook", "Concluir um lote de ração", "start_cook"),
    ("coop", "quintal_egg", "Recolher ovos prontos", "quintal_collect_eggs"),
    ("pasture", "quintal_milk_collection", "Ordenhar uma vaca pronta", "quintal_collect_milk"),
    ("pier", "quintal_fish", "Completar uma captura válida", "start_fish"),
)


def routine_activity(events, spaces):
    metrics = {metric for space, metric, _, _ in ROUTINE_OPTIONS if space in spaces}
    return [e for e in valid_events(events)
            if (e.evidence or {}).get("local_day") and not e.evidence.get("historical_undated")
            and ((e.kind == "care" and "care" in metrics) or
                 (e.kind == "quintal" and e.evidence.get("metric") in metrics
                  and int(e.evidence.get("quantity", 0)) > 0))]


def project(events, prefs, now=None):
    period = calendar(now)
    valid = valid_events(events)
    complete = {e.event_key: e for e in events if e.kind in {"mission", "challenge", "achievement"}
                and e.state == "confirmed"}
    def objective(mid, title, desc, metric, target, xp, coins, kind, bucket, points=0):
        rows = valid if kind in {"initial", "achievement"} else [e for e in valid
            if e.evidence.get("local_day" if kind == "daily" else "local_week") == bucket]
        if metric in {"quintal_routine", "quintal_routine_days"}:
            activity = routine_activity(rows, prefs.get("quintal_routine_spaces", []))
            value = len({e.evidence["local_day"] for e in activity}) if metric.endswith("_days") else len(activity)
        else:
            value = counters(rows).get(metric, 0)
        key = f"challenge:{mid}" if kind == "achievement" else f"mission:{mid}:{bucket}"
        paid = complete.get(key)
        reset = period["daily_reset_at" if kind == "daily" else "weekly_reset_at"] if kind in {"daily", "weekly"} else None
        unit = {"quintal_routine": "atividades à escolha", "quintal_routine_days": "dias de atividade", "individuals": "gatos diferentes", "photos": "fotos válidas", "complements": "complementos úteis",
                "photo_days": "dias com registros", "care": "cuidados", "care_days": "dias de cuidado",
                "community": "contribuições", "colors": "cores confirmadas", "clues": "pistas reconhecidas", "reviews": "revisões",
                "quintal_cat_set": "cantinhos completos", "quintal_water_cat": "abastecimentos", "quintal_harvest": "colheitas", "quintal_cook": "lotes preparados", "quintal_meal": "refeições", "quintal_fish": "capturas", "quintal_facility": "instalações concluídas", "quintal_favorite": "receitas favoritas", "quintal_bond": "pontos de vínculo"}[metric]
        return dict(id=mid, instance_id=key, event_key=key, title=title, description=desc, metric=metric,
                    unit=unit, target=target, progress=target if paid else min(value, target), type=kind,
                    xp_reward=getattr(paid, "xp", xp) if paid and not paid.evidence.get("migration") else xp,
                    coin_reward=getattr(paid, "coins", coins) if paid and not paid.evidence.get("migration") else coins,
                    completed=paid is not None, unlocked=paid is not None,
                    unlocked_at=paid.created_at.isoformat() if paid and paid.created_at else None,
                    reward_received=paid is not None, xp_received=getattr(paid, "xp", 0) if paid and not paid.evidence.get("migration") else None,
                    coin_received=getattr(paid, "coins", 0) if paid and not paid.evidence.get("migration") else None,
                    legacy_reward_unknown=bool(paid and paid.evidence.get("migration")), period=bucket, reset_at=reset, ranking_points=points,
                    destination="habitat" if metric.startswith("quintal_") else {"care": "habitat", "care_days": "habitat", "community": "social", "clues": "missing",
                                 "reviews": "collection"}.get(metric, "capture"),
                    status="completed" if paid else "in_progress" if value else "available")
    daily = [objective("daily_record", "Guarde uma nova história", "Descubra um gato ou acrescente uma foto útil a uma ficha.",
                "photos", 1, 20, 10, "daily", period["day"], 10)]
    offer = prefs.get("milestone_daily_offers", {}).get(period["day"], "album")
    if offer == "care":
        daily.append(objective("daily_care", "Um momento de cuidado", "Faça carinho, brinque ou cuide dos potes de um gato no Quintal.",
                     "care", 1, 15, 10, "daily", period["day"], 10))
    else:
        daily.append(objective("daily_album", "Mais um detalhe", "Acrescente um complemento útil; outro ângulo ajuda a reconhecer o gato.",
                     "complements", 1, 20, 10, "daily", period["day"], 10))
    daily.append(objective("daily_community", "Uma história para compartilhar", "Publique, comente ou compartilhe um cartaz ativo dentro do app.",
                 "community", 1, 10, 5, "daily", period["day"], 10))
    weekly = [objective("weekly_days", "Histórias ao longo da semana", "Guarde fotos válidas em três dias diferentes, sem exigir dias seguidos.",
                    "photo_days", 3, 100, 50, "weekly", period["week"], 30),
              objective("weekly_details", "Enriqueça seu álbum", "Acrescente quatro complementos úteis durante esta semana.",
                    "complements", 4, 120, 60, "weekly", period["week"], 30),
              objective("weekly_discover", "Novos encontros", "Confirme três gatos diferentes nesta semana.",
                    "individuals", 3, 150, 75, "weekly", period["week"], 30)]
    spaces = prefs.get("quintal_routine_spaces", [])
    alternatives = [dict(space=space, metric=metric, title=label, tutorial_anchor=anchor)
                    for space, metric, label, anchor in ROUTINE_OPTIONS if space in spaces]
    for kind, mid, title, desc, metric, target, xp, coins, bucket, output in (
        ("daily", "daily_quintal_routine", "Uma pausa no Quintal",
         "Se couber na sua visita, escolha uma das atividades abaixo. Uma só basta; produtos precisam estar prontos.",
         "quintal_routine", 1, 10, 5, period["day"], daily),
        ("weekly", "weekly_quintal_routine", "No seu ritmo",
         "Escolha uma atividade em dois dias desta semana. Não precisam ser seguidos, nem usar espaços diferentes.",
         "quintal_routine_days", 2, 30, 15, period["week"], weekly),
    ):
        if alternatives or f"mission:{mid}:{bucket}" in complete:
            row = objective(mid, title, desc, metric, target, xp, coins, kind, bucket)
            row.update(optional=True, group="quintal_routine", alternatives=alternatives,
                       tutorial_anchor=mid, visit_hint="Uma atividade nesta visita" if kind == "daily"
                       else "Duas visitas em dias diferentes", selection_mode="any")
            output.append(row)
    initial = [objective("start_record", "Seu primeiro encontro", "Registre uma foto e confirme a identidade do gato.", "photos", 1, 25, 15, "initial", "once"),
               objective("start_details", "Conheça melhor seu gato", "Acrescente uma foto com detalhes ou outro ângulo.", "complements", 1, 25, 15, "initial", "once"),
               objective("start_review", "Seu olhar também conta", "Revise as características sugeridas pela IA na tela de resultado.", "reviews", 1, 20, 10, "initial", "once"),
               objective("start_care", "Um lar para cuidar", "Coloque um gato próprio no quintal e realize um cuidado válido.", "care", 1, 25, 15, "initial", "once"),
               objective("start_community", "Encontre sua comunidade", "Contribua com uma publicação, comentário ou cartaz. Esta etapa é opcional.", "community", 1, 20, 10, "initial", "once")]
    initial.extend([
        objective("start_bowls", "Um cantinho só seu", "Coloque um gato junto com seus potes de comida e água.", "quintal_cat_set", 1, 15, 10, "initial", "once"),
        objective("start_water", "Água fresquinha", "Abasteça o pote de água do seu gato.", "quintal_water_cat", 1, 10, 5, "initial", "once"),
        objective("start_harvest", "Primeira colheita", "Abra a horta, plante e colha um canteiro.", "quintal_harvest", 1, 20, 10, "initial", "once"),
        objective("start_cook", "Feito com carinho", "Prepare seu primeiro lote de ração na cozinha.", "quintal_cook", 1, 20, 10, "initial", "once"),
        objective("start_meal", "Conhecendo os gostos", "Tenha uma refeição consumida por um gato.", "quintal_meal", 1, 15, 10, "initial", "once"),
        objective("start_fish", "Primeira pescaria", "Conserte o píer e complete uma captura válida.", "quintal_fish", 1, 20, 10, "initial", "once"),
    ])
    for index, row in enumerate(initial):
        row.update(optional=index == 4, tutorial_anchor=row["id"], prerequisite=initial[index-1]["id"] if index else None)
    initial[5]["prerequisite"] = "start_care"
    achievements = [objective(mid, title, desc, metric, target, xp, coins, "achievement", "lifetime", points)
                    for mid, title, desc, metric, target, xp, coins, points in ACHIEVEMENTS]
    for mid, date in prefs.get("preserved_achievements", {}).items():
        known = next((r for r in achievements if r["id"] == mid), None)
        if known:
            known.update(completed=True, unlocked=True, progress=known["target"], unlocked_at=date, reward_received=True, legacy_reward_unknown=known["legacy_reward_unknown"] or known["xp_received"] is None)
        else:
            achievements.append(dict(id=mid, title=LEGACY_ACHIEVEMENTS.get(mid, ("Conquista histórica", "", 1))[0],
                description=LEGACY_ACHIEVEMENTS.get(mid, ("", "Marco preservado no seu histórico.", 1))[1], type="achievement", completed=True, unlocked=True,
                target=1, progress=1, unit="marco", unlocked_at=date, archived=True, xp_reward=0, coin_reward=0,
                ranking_points=0, reward_received=True, legacy_reward_unknown=True, status="completed"))
    return {"calendar": period, "daily_missions": daily, "weekly_missions": weekly,
            "initial_missions": initial, "achievements": achievements, "challenges": [],
            "season": None, "confirmed_individuals": counters(valid)["individuals"]}


def scores(events, prefs, now=None):
    view = project(events, prefs, now)
    week = view["calendar"]["week"]
    rows = valid_events(events)
    weekly = [e for e in rows if e.evidence.get("local_week") == week]
    missions = sum(int(e.evidence.get("ranking_points", 0)) for e in events
                   if e.kind == "mission" and e.state == "confirmed" and e.evidence.get("objective_type") in {"daily", "weekly"}
                   and e.evidence.get("local_week") == week)
    medals = sum(r["ranking_points"] for r in view["achievements"] if r["completed"])
    medals += sum(int(e.evidence.get("ranking_points", 0)) for e in events if e.kind == "season_challenge" and e.state == "confirmed")
    return {"cats_weekly": counters(weekly)["individuals"], "cats_all": counters(rows)["individuals"],
            "missions": missions, "milestones": medals}
