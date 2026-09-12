from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tfwp:tfwp@localhost:5432/tfwp"

    # Where the quarterly source files live. Mounted into the api container so
    # the loader and the host see the same paths.
    raw_data_dir: Path = Path("data/raw")

    default_page_size: int = 50
    max_page_size: int = 500

    # The quarter from which 'PR Only' LMIAs began appearing in the published
    # list. Earlier lists were not retroactively updated, so any series that
    # spans this boundary has a structural break in it. See app/api/trends.py.
    pr_only_from_quarter: str = "2023Q4"


settings = Settings()
