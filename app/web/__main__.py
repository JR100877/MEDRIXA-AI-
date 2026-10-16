"""Local developer entry point. Configuration comes only from MEDRIXA_* variables.

    python -m app.web issue-token --sub dev-user --role case_runner --case demo-synthetic-001
    python -m app.web serve --port 8000        # binds 127.0.0.1 only; needs uvicorn

There is no production profile and no option to bind a public interface.
"""
import argparse
import sys

from app.web.auth import issue_token
from app.web.policy import ROLE_PERMISSIONS
from app.web.settings import ConfigurationError, settings_from_env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.web", description="MEDRIXA API local (provisional)")
    commands = parser.add_subparsers(dest="command", required=True)
    token = commands.add_parser("issue-token", help="Emitir token HMAC local de desarrollo/prueba")
    token.add_argument("--sub", required=True)
    token.add_argument("--role", action="append", required=True, choices=sorted(ROLE_PERMISSIONS))
    token.add_argument("--case", action="append", default=[])
    token.add_argument("--ttl", type=int, default=900)
    serve = commands.add_parser("serve", help="Servir la API en 127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        settings = settings_from_env()
    except ConfigurationError as error:
        print(f'{{"error":"CONFIGURATION","detail":"{error}"}}', file=sys.stderr)
        return 2
    if args.command == "issue-token":
        if settings.auth_secret is None:  # oidc mode: tokens come from the identity provider
            print('{"error":"ISSUE_TOKEN_REQUIRES_HMAC_MODE"}', file=sys.stderr)
            return 2
        if not 60 <= args.ttl <= settings.token_max_ttl_seconds:
            print('{"error":"INVALID_TTL"}', file=sys.stderr)
            return 2
        try:
            print(issue_token(settings.auth_secret.get_secret_value(), args.sub, args.role, args.case, args.ttl))
        except ValueError:
            print('{"error":"INVALID_INPUT"}', file=sys.stderr)
            return 2
        return 0
    try:
        import uvicorn
    except ImportError:
        print('{"error":"UVICORN_NOT_INSTALLED"}', file=sys.stderr)
        return 2
    from app.web.app import create_app
    from app.web.audit import configure_audit_logging
    configure_audit_logging()  # without it every audited operation fails closed
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, server_header=False,
                proxy_headers=False, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
