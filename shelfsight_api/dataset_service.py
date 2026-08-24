from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, insert

from shelfsight_api.models import dataset_versions, datasets


def create_dataset_with_open_version(
    connection: Connection,
    name: str,
    description: str | None,
) -> dict[str, Any]:
    dataset_id = uuid4()
    version_id = uuid4()
    connection.execute(
        insert(datasets).values(
            id=dataset_id,
            name=name,
            description=description,
        )
    )
    connection.execute(
        insert(dataset_versions).values(
            id=version_id,
            dataset_id=dataset_id,
        )
    )
    return {
        "id": dataset_id,
        "name": name,
        "description": description,
        "open_version_id": version_id,
    }
