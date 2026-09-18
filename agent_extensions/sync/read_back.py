"""Read-back logic to reconstruct live catalog/profile state from filesystem."""

import json
from pathlib import Path
from typing import Union
from agent_extensions.schemas.catalog_schema import Catalog
from agent_extensions.schemas.profile_schema import Profile


def read_catalog_from_filesystem(catalog_file_path: Union[str, Path]) -> Catalog:
    """Reconstruct the live catalog from filesystem JSON.

    Args:
        catalog_file_path: Path to catalog.json file

    Returns:
        Populated Catalog object

    Raises:
        FileNotFoundError: If catalog file does not exist
        json.JSONDecodeError: If file is not valid JSON
        ValueError: If catalog data fails validation
    """
    path = Path(catalog_file_path)

    if not path.exists():
        raise FileNotFoundError(f"Catalog file not found: {path}")

    data = json.loads(path.read_text())
    return Catalog(**data)


def read_profile_from_filesystem(profile_file_path: Union[str, Path]) -> Profile:
    """Reconstruct the active profile from filesystem JSON.

    Args:
        profile_file_path: Path to profile.json file

    Returns:
        Populated Profile object
    """
    path = Path(profile_file_path)

    if not path.exists():
        raise FileNotFoundError(f"Profile file not found: {path}")

    data = json.loads(path.read_text())
    return Profile(**data)


def offline_bootstrap(catalog_dir: Union[str, Path]) -> dict:
    """Bootstrap catalog/profile without network access.

    Reads all JSON files in a directory and returns a dict of loaded schemas.
    Used for hermetic/offline operation.

    Args:
        catalog_dir: Directory containing catalog.json and profile.json

    Returns:
        Dict with 'catalog' and 'profile' keys
    """
    dir_path = Path(catalog_dir)

    catalog = read_catalog_from_filesystem(dir_path / "catalog.json")
    profile = read_profile_from_filesystem(dir_path / "profile.json")

    return {
        "catalog": catalog,
        "profile": profile,
    }
