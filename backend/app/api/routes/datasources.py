"""Data source endpoints. HTTP concerns only — behaviour lives in the service."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import ConnectionFailed, ConnectionTestResult, ConnectorError
from app.core.crypto import CryptoError
from app.db.models import DataSource, TableRelationship
from app.db.session import get_session
from app.schemas.datasource import (
    ColumnOut,
    ConnectionTestRequest,
    DataSourceCreate,
    DataSourceOut,
    RelationshipOut,
    SchemaOut,
    SyncResult,
    TableOut,
)
from app.services import datasource_service as service

router = APIRouter(prefix="/datasources", tags=["datasources"])


def _out(source: DataSource, table_count: int = 0) -> DataSourceOut:
    return DataSourceOut(
        id=source.id,
        name=source.name,
        type=source.type,
        config=source.config,
        has_secret=source.has_secret,
        status=source.status,
        status_message=source.status_message,
        business_context=source.business_context,
        table_count=table_count,
        last_synced_at=source.last_synced_at,
    )


def _secret(value) -> str | None:
    return value.get_secret_value() if value else None


@router.post("/test", response_model=ConnectionTestResult)
async def test_connection(body: ConnectionTestRequest) -> ConnectionTestResult:
    return await service.test_connection(
        body.type, body.config.model_dump(), _secret(body.password)
    )


@router.post("", response_model=DataSourceOut, status_code=status.HTTP_201_CREATED)
async def create_data_source(
    body: DataSourceCreate, session: AsyncSession = Depends(get_session)
) -> DataSourceOut:
    try:
        source = await service.create_data_source(
            session,
            name=body.name,
            source_type=body.type,
            config=body.config.model_dump(),
            password=_secret(body.password),
            business_context=body.business_context,
        )
        await session.commit()
    except service.DataSourceNameTaken as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ConnectionFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.message) from exc
    except CryptoError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    tables = await service.get_schema(session, source.id)
    return _out(source, len(tables))


@router.get("", response_model=list[DataSourceOut])
async def list_data_sources(session: AsyncSession = Depends(get_session)) -> list[DataSourceOut]:
    return [_out(source, count) for source, count in await service.list_data_sources(session)]


@router.get("/{data_source_id}", response_model=DataSourceOut)
async def get_data_source(
    data_source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DataSourceOut:
    try:
        source = await service.get_data_source(session, data_source_id)
    except service.DataSourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.") from exc
    return _out(source, len(await service.get_schema(session, data_source_id)))


@router.delete("/{data_source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_source(
    data_source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await service.delete_data_source(session, data_source_id)
        await session.commit()
    except service.DataSourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.") from exc


@router.post("/{data_source_id}/sync", response_model=SyncResult)
async def sync_data_source(
    data_source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SyncResult:
    try:
        result = await service.sync_data_source(session, data_source_id)
        await session.commit()
        return result
    except service.DataSourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.") from exc
    except ConnectorError as exc:
        await session.commit()  # persist the error status set by the service
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message) from exc


@router.get("/{data_source_id}/schema", response_model=SchemaOut)
async def get_schema(
    data_source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SchemaOut:
    try:
        tables = await service.get_schema(session, data_source_id)
    except service.DataSourceNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.") from exc

    relationships = (
        await session.scalars(
            select(TableRelationship).where(TableRelationship.data_source_id == data_source_id)
        )
    ).all()

    return SchemaOut(
        tables=[
            TableOut(
                id=table.id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                description=table.description,
                row_count_estimate=table.row_count_estimate,
                is_queryable=table.is_queryable,
                columns=[
                    ColumnOut(
                        name=column.name,
                        data_type=column.data_type,
                        normalized_type=column.normalized_type,
                        description=column.description,
                        sample_values=column.sample_values,
                        distinct_count=column.distinct_count,
                        is_dimension=column.is_dimension,
                    )
                    for column in table.columns
                ],
            )
            for table in tables
        ],
        relationships=[
            RelationshipOut(
                from_table=r.from_table,
                from_column=r.from_column,
                to_table=r.to_table,
                to_column=r.to_column,
                source=r.source,
            )
            for r in relationships
        ],
    )
