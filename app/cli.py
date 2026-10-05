"""Management commands (Django equivalent: manage.py).

python -m app.cli create-admin --email director@example.com --name "Academy Director"
"""

import asyncio
import getpass
import sys

import typer
from sqlalchemy import select

import app.db_models  # noqa: F401
from app.core.database import get_engine, get_sessionmaker
from app.core.security import hash_password
from app.modules.auth.schemas import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from app.modules.users.models import Role, User

cli = typer.Typer(no_args_is_help=True, help="MSRF backend management commands")


@cli.callback()
def _main() -> None:
    """Keeps sub-command syntax (`create-admin`) even while there is only one command."""


async def _create_admin(email: str, name: str, password: str) -> None:
    async with get_sessionmaker()() as db:
        if await db.scalar(select(User.id).where(User.email == email)):
            raise typer.BadParameter(f"A user with email {email} already exists")
        db.add(
            User(
                email=email,
                full_name=name,
                role=Role.ADMIN,
                password_hash=hash_password(password),
                is_verified=True,
            )
        )
        await db.commit()
    await get_engine().dispose()


@cli.command("create-admin")
def create_admin(
    email: str = typer.Option(..., help="Login email"),
    name: str = typer.Option(..., help="Full name"),
    password_stdin: bool = typer.Option(False, "--password-stdin", help="Read the password from stdin"),
) -> None:
    """Create an ADMIN account. The password is prompted for (or read from stdin), never an argument."""
    if password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            raise typer.BadParameter("Passwords do not match")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise typer.BadParameter(f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters")
    asyncio.run(_create_admin(email.strip().lower(), name.strip(), password))
    typer.echo(f"Admin {email} created.")


@cli.command("seed-dev")
def seed_dev() -> None:
    """Load demo data (development only). Prints the generated demo passwords once."""
    from app.seed import seed

    async def _run() -> list[tuple[str, str, str]]:
        async with get_sessionmaker()() as db:
            accounts = await seed(db)
        await get_engine().dispose()
        return accounts

    accounts = asyncio.run(_run())
    if not accounts:
        typer.echo("Seed data already present; nothing to do.")
        return
    typer.echo("Demo data loaded. Demo accounts (save these passwords; they are not stored anywhere):")
    for role, email, password in accounts:
        typer.echo(f"  {role:<6} {email:<28} {password}")


@cli.command("seed-demo-activity")
def seed_demo_activity() -> None:
    """Add months of demo activity (fees, payments, sessions, reports, gallery, ...). Development only."""
    from app.demo_data import seed_activity

    async def _run() -> dict[str, int]:
        async with get_sessionmaker()() as db:
            stats = await seed_activity(db)
        await get_engine().dispose()
        return stats

    stats = asyncio.run(_run())
    if not stats:
        typer.echo("Demo activity already present; nothing to do.")
        return
    typer.echo("Demo activity added:")
    for key, value in stats.items():
        typer.echo(f"  {key:<22} {value}")


@cli.command("generate-ledger")
def generate_ledger() -> None:
    """Create this month's fee entries now (the scheduler also does this daily)."""
    from app.core.db_sync import sync_session
    from app.tasks.jobs import generate_ledger as run

    with sync_session() as db:
        typer.echo(f"Created {run(db)} ledger entries.")


if __name__ == "__main__":
    cli()
