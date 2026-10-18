"""Disposable SQLite renderer fixtures. Never touches a player or production DB."""
import asyncio, json, sys
from pathlib import Path
from uuid import UUID, uuid4
from datetime import UTC, datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.persistence_models import User, HabitatWallet, HabitatItem, HabitatTransaction
from app.quintal_models import QuintalFacility, QuintalResource, QuintalTask, QuintalPlot, QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession
from app.services.quintal_production import projection, stock, ensure_pair, iso
from app.services.habitat_service import SHOP, yard_cells
from app.services.habitat_pricing import shop_item

async def main(target, mode='active'):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for model in (User, HabitatWallet, HabitatItem, HabitatTransaction, QuintalFacility, QuintalResource, QuintalTask, QuintalPlot, QuintalCounter, QuintalResident, QuintalCatCare, QuintalPreference, QuintalSession):
            await connection.run_sync(model.__table__.create)
    now = datetime(2026,10,6,15,tzinfo=UTC)
    uid, cat = uuid4(), UUID('99999999-1111-4111-8111-999999999999')
    async with async_sessionmaker(engine,expire_on_commit=False)() as session:
        wallet = HabitatWallet(user_id=uid,balance=250,expansion_level=3,selected_cat_ids=[str(cat)],cat_anchors={str(cat):[2,4]},care_state={})
        session.add_all([User(id=uid,email='renderer-only@test.local'),wallet])
        await session.flush()
        care = await ensure_pair(session,wallet,cat)
        for role, position in (('food_bowl',(4,4)),('water_bowl',(4,5))):
            item = await session.get(HabitatItem,UUID(care.state[role]))
            item.x,item.y,item.rotation = *position,0
        care.state = {**care.state,'assisted':False,'affinity':36,'last_meal_at':iso(now-timedelta(hours=10)),'served_at':iso(now-timedelta(hours=10)),'recipe':'garden','portions':1,'water_until':iso(now+timedelta(hours=8))}
        session.add(QuintalPreference(user_id=uid,cat_id=cat,recipe='garden',state={'level':5,'known':True}))
        for kind,level in {'garden':3,'kitchen':2,'pier':1,'rod':2,'market':1,'coop':2,'pasture':2,'orchard':1}.items():
            building = mode == 'repairing' and kind == 'coop'
            session.add(QuintalFacility(user_id=uid,kind=kind,level=0 if mode == 'abandoned' or building else level,target_level=1 if building else 0,ready_at=now+timedelta(minutes=30) if building else None))
        for slot in range(0 if mode == 'abandoned' else 12):
            crop = ['corn','wheat','carrot','pumpkin'][slot%4] if slot<5 else None
            session.add(QuintalPlot(user_id=uid,slot=slot,crop=crop,watered=1 if crop else 0,started_at=now-timedelta(hours=1) if crop else None,ready_at=now+timedelta(minutes=15) if slot==1 else now-timedelta(minutes=1) if crop else None))
        for kind,count in (('coop',4),('pasture',2),('orchard',2)):
            if mode == 'abandoned' or mode == 'repairing' and kind == 'coop': continue
            session.add(QuintalResident(user_id=uid,kind=kind,state={'count':count,'cursor':iso(now-timedelta(hours=10)),'food_start':iso(now-timedelta(hours=10)),'food_until':iso(now+timedelta(hours=14)),'water_start':iso(now-timedelta(hours=10)),'water_until':iso(now+timedelta(hours=14)),'names':{'0':'Pipoca','1':'Caramelo'} if kind=='coop' else {'0':'Mimosa','1':'Flor'}}))
        if mode != 'abandoned':
            session.add(QuintalTask(id=uuid4(),user_id=uid,kind='cook',target='garden',status='active',started_at=now-timedelta(minutes=20),ready_at=now-timedelta(minutes=10),inputs={'wheat':2,'carrot':1},outputs={'ration:garden':3}))
        await stock(session,uid,{'corn':12,'wheat':18,'carrot':8,'fish':3,'egg':4,'milk':2,'ration:garden':6,'ration:fish':3})
        state = await projection(session,uid,now)
        items = await session.run_sync(lambda sync: list(sync.query(HabitatItem).filter_by(user_id=uid)))
        habitat = {'care':{'server_now':iso(now),'day':'2026-10-06','cats':{str(cat):{'affinity':36}}},'balance':250,'revision':0,'expansion_level':3,'free_shop':True,'layout_rules_version':3,'selected_cat_ids':[str(cat)],'cat_anchors':{str(cat):[2,4]},'buildable_cells':[list(c) for c in sorted(yard_cells(3))],'cats':[{'id':str(cat),'name':'Mimi'}],'items':[{'id':str(i.id),'sku':i.sku,'x':i.x,'y':i.y,'rotation':i.rotation,'care_state':i.care_state or {}} for i in items],'satisfaction':{'value':60}}
        document={'notice':'Dados exclusivos da galeria de testes. Criados em SQLite descartável, sem modificar contas reais.','production':state,'habitat':habitat,'shop':{'items':[shop_item(k,v) for k,v in SHOP.items()]}}
        Path(target).write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
    await engine.dispose()

if __name__=='__main__': asyncio.run(main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'active'))
