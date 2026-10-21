"""Server-clock production. Callers hold the wallet lock until commit/rollback."""

from datetime import timedelta
from uuid import UUID, uuid4
from app.quintal_models import QuintalTask
from app.services.quintal_catalog import RECIPES, ORDERS, order_product_options


from app.services.quintal_clock import utc
from app.services.quintal_inventory import rows, counter, stock
from .context import ProductionCommand, CommandOutcome


async def cook(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    data = command.data
    now = command.now
    wallet = command.wallet
    lv = command.lv
    result = {}
    price = 0
    tasks = await rows(session, QuintalTask, user_id)
    active = sorted(
        [t for t in tasks if t.kind == action and t.status == "active"],
        key=lambda t: utc(t.ready_at),
    )
    if action == "cook":
        definition = RECIPES.get(target)
        if (
            not definition
            or lv.get("kitchen", 0) < 1
            or lv.get(definition[5], 0) < definition[6]
        ):
            raise ValueError("Receita ainda não liberada.")
        if target in ("beef", "special") and lv.get("market", 0) < 1:
            raise ValueError("Abra o mercado para liberar esta receita.")
        if len(active) >= (2 if lv.get("kitchen", 0) == 1 else 4):
            raise ValueError("Fila da cozinha cheia.")
        inputs, seconds, outputs = (
            definition[1],
            definition[2],
            {"ration:" + target: definition[3]},
        )
    else:
        definition = ORDERS.get(target)
        if (
            not definition
            or lv.get("market", 0) < 1
            or wallet.expansion_level < definition[5]
        ):
            raise ValueError("Encomenda não liberada.")
        if any((t.kind == "order" and t.status in ("active", "ready") for t in tasks)):
            raise ValueError("Colete a encomenda anterior primeiro.")
        if data.get("payment") not in ("coins", "products"):
            raise ValueError("Escolha moedas ou produtos.")
        quantity = data.get("quantity", definition[4])
        if (
            type(quantity) is not int
            or not definition[4] <= quantity <= 100
            or quantity % definition[4]
        ):
            raise ValueError(
                f"Escolha de {definition[4]} a 100 unidades, em lotes de {definition[4]}."
            )
        batches = quantity // definition[4]
        inputs = {}
        if data["payment"] == "products":
            option = next(
                (
                    o
                    for o in order_product_options(target)
                    if o["id"] == data.get("product_option", "harvest")
                ),
                None,
            )
            if option is None:
                raise ValueError("Troca de produtos inválida.")
            if (
                option.get("facility")
                and lv.get(option["facility"], 0) < option["level"]
            ):
                raise ValueError("Recupere o pomar para trocar suas frutas.")
            inputs = {k: v * batches for k, v in option["products"].items()}
        elif data.get("product_option") is not None:
            raise ValueError("Escolha moedas ou uma troca de produtos.")
        price = (
            command.price_for(definition[1]) * batches
            if data["payment"] == "coins"
            else 0
        )
        if wallet.balance < price:
            raise ValueError("Moedas insuficientes.")
        seconds, outputs = (definition[3] * batches, {target: quantity})
    await stock(session, user_id, {k: -v for k, v in inputs.items()})
    start = max(now, utc(active[-1].ready_at)) if active else now
    task = QuintalTask(
        id=uuid4(),
        user_id=user_id,
        kind=action,
        target=target,
        status="active",
        started_at=start,
        ready_at=start + timedelta(seconds=seconds),
        inputs=inputs,
        outputs=outputs,
    )
    session.add(task)
    result = {"task_id": str(task.id)}
    return CommandOutcome(result, price)


async def collect(command: ProductionCommand):
    session = command.session
    user_id = command.user_id
    action = command.action
    target = command.target
    now = command.now
    result = {}
    price = 0
    try:
        task = await session.get(QuintalTask, UUID(target))
    except ValueError as error:
        raise ValueError("Tarefa inválida.") from error
    if not task or task.user_id != user_id:
        raise ValueError("Tarefa inexistente.")
    if action == "collect":
        if task.status != "ready":
            raise ValueError("Tarefa ainda não pronta ou já coletada.")
        await stock(session, user_id, task.outputs)
        task.status = "collected"
        await counter(session, user_id, "collected:" + task.kind, 1)
    else:
        if (
            task.kind != "cook"
            or task.status != "active"
            or utc(task.started_at) <= now
        ):
            raise ValueError("Só preparos em espera podem ser cancelados.")
        await stock(session, user_id, task.inputs)
        task.status = "cancelled"
        duration = utc(task.ready_at) - utc(task.started_at)
        for following in await rows(session, QuintalTask, user_id):
            if (
                following.kind == "cook"
                and following.status == "active"
                and (utc(following.started_at) >= utc(task.ready_at))
            ):
                following.started_at = utc(following.started_at) - duration
                following.ready_at = utc(following.ready_at) - duration
    return CommandOutcome(result, price)
