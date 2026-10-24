"""Operator commands. Run from backend/:

    uv run python -m paymentsapp.cli generate-encryption-key
    uv run python -m paymentsapp.cli create-admin --login-id platform_admin --name "Your Name" --roles platform_admin
    uv run python -m paymentsapp.cli list-admins

create-admin asks for the password (it is never passed on the command line, where it would land in shell history)
and prints the TOTP secret once, to add to an authenticator app.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.crypto import generate_key
from paymentsapp.core.security import totp_uri
from paymentsapp.db.session import get_engine
from paymentsapp.modules.admin.models import AdminUser
from paymentsapp.modules.admin.service import AdminRole, create_admin
from paymentsapp.modules.plugins.service import seed_plugins


async def _create_admin(login_id: str, name: str, roles: set[AdminRole]) -> int:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("passwords do not match", file=sys.stderr)
        return 1
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            created = await create_admin(session, login_id, name, password, roles)
        except ValueError as exc:
            print(f"not created: {exc}", file=sys.stderr)
            return 1
        await session.commit()
    await engine.dispose()

    print(f"Created admin {created.user.id} ({created.user.login_id}) with roles {', '.join(created.user.roles)}.")
    print("Add this to an authenticator app now. It is shown only once:")
    print(f"  secret: {created.totp_secret}")
    print(f"  or scan/open: {totp_uri(created.totp_secret, created.user.login_id)}")
    return 0


async def _list_admins() -> int:
    engine = get_engine()
    async with AsyncSession(engine) as session:
        for user in (await session.scalars(select(AdminUser).order_by(AdminUser.created_at))).all():
            print(f"{user.id}  {user.status:<8}  {','.join(user.roles):<35}  {user.login_id}")
    await engine.dispose()
    return 0


async def _seed_plugins() -> int:
    engine = get_engine()
    async with AsyncSession(engine) as session:
        created = await seed_plugins(session)
        await session.commit()
    await engine.dispose()
    print(f"created: {', '.join(created)}" if created else "all plugins already exist; nothing changed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paymentsapp.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("generate-encryption-key", help="print a new FIELD_ENCRYPTION_KEYS value")

    create = commands.add_parser("create-admin", help="create an admin website account")
    create.add_argument("--login-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--roles", required=True, help=f"comma-separated, from: {', '.join(r.value for r in AdminRole)}"
    )

    commands.add_parser("list-admins", help="list admin accounts")
    commands.add_parser("seed-plugins", help="create plugin settings, importing mock bank values from .env once")

    args = parser.parse_args(argv)
    if args.command == "generate-encryption-key":
        print(f"FIELD_ENCRYPTION_KEYS=k1={generate_key()}")
        return 0
    if args.command == "create-admin":
        try:
            roles = {AdminRole(r.strip()) for r in args.roles.split(",") if r.strip()}
        except ValueError as exc:
            parser.error(str(exc))
        return asyncio.run(_create_admin(args.login_id, args.name, roles))
    if args.command == "seed-plugins":
        return asyncio.run(_seed_plugins())
    return asyncio.run(_list_admins())


if __name__ == "__main__":
    raise SystemExit(main())
