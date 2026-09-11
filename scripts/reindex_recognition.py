"""Queue current photographs after a model/preprocessing upgrade."""
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.database import get_session_factory
from app.persistence_models import CatPhoto, RemoteCat
from app.services.vision_jobs import enqueue_recognition


async def run(apply: bool):
    async with get_session_factory()() as session:
        rows = (await session.execute(select(RemoteCat, CatPhoto).join(
            CatPhoto, CatPhoto.cat_id == RemoteCat.id).where(
                RemoteCat.deleted_at.is_(None), CatPhoto.deleted_at.is_(None)))).all()
        print(f"Fotos atuais: {len(rows)}")
        if apply:
            for cat, photo in rows:
                await enqueue_recognition(session, cat=cat, photo=photo)
            await session.commit()
            print("Reconhecimento enfileirado.")
        else:
            print("Prévia. Use --apply para enfileirar.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args().apply))
