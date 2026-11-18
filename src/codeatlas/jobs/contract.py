"""Validated, versioned inputs captured when an evaluation is submitted."""

from typing import Literal

from pydantic import Field

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.bundle.hashing import sha256_hex
from codeatlas.db_models import Connection
from codeatlas.schema import IdentityMap, Policy, UtcDatetime
from codeatlas.schema.types import Model


class SubmissionContract(Model):
    version: Literal[2] = 2
    source_mode: Literal["live", "fixtures"]
    jira_connection_id: str = Field(min_length=1)
    github_connection_id: str = Field(min_length=1)
    jira_connection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    github_connection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    jira_project: str = Field(min_length=1)
    policy: Policy
    policy_source: str = Field(min_length=1)
    identity_map: IdentityMap
    identity_map_source: str = Field(min_length=1)
    cutoff: UtcDatetime


def connection_hash(connection: Connection) -> str:
    """Identify the exact stored configuration and encrypted credential, without exposing either."""
    return sha256_hex(
        canonical_bytes(
            {
                "id": connection.id,
                "provider": connection.provider,
                "configuration": connection.configuration,
                "encrypted_credential": connection.encrypted_credential,
            }
        )
    )
