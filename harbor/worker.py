"""Durable reminder/hold worker; local outbox only, no network delivery adapters."""

import time
from .api import create_app


def run_once(app):
    state = app.state
    state.engine.expire_holds()
    state.engine.process_waitlist(
        consent_check=lambda pid, channel, loc: state.workflows.allowed(
            pid, loc, channel, state.packs[loc]["location"]["timezone"]
        )
    )
    return state.workflows.tick(state.packs)


def main():
    app = create_app()
    try:
        while True:
            run_once(app)
            time.sleep(15)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
