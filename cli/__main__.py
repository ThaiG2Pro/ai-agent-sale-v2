"""CLI entry point."""

import click

from cli.memory import memory


@click.group()
def cli():
    """AI Sales Agent CLI."""
    pass


cli.add_command(memory)


if __name__ == "__main__":
    cli()
