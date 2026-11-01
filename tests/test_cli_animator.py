from unittest.mock import MagicMock, patch

from rich.table import Table

from panoptis.cli.animator import (
    animate_table_rows,
    cinematic_logo_reveal,
    glitch_ascii_art,
    matrix_type_print,
    print_systematic_menu,
)


@patch("panoptis.cli.animator.time.sleep", return_value=None)
@patch("panoptis.cli.animator.random.choice", return_value="X")
@patch("panoptis.cli.animator.sys.stdout.flush")
@patch("panoptis.cli.animator.sys.stdout.write")
def test_matrix_type_print(
    mock_write,
    mock_flush,
    mock_choice,
    mock_sleep,
):
    matrix_type_print("Hello")

    assert mock_write.called
    assert mock_flush.called


@patch("panoptis.cli.animator.time.sleep", return_value=None)
@patch("panoptis.cli.animator.random.choice", return_value="X")
@patch("panoptis.cli.animator.sys.stdout.flush")
@patch("panoptis.cli.animator.sys.stdout.write")
def test_cinematic_logo_reveal(
    mock_write,
    mock_flush,
    mock_choice,
    mock_sleep,
):
    cinematic_logo_reveal(
        [
            "AGENTWATCH",
            "CLI",
        ]
    )

    assert mock_write.called
    assert mock_flush.called


@patch("panoptis.cli.animator.Live")
@patch("panoptis.cli.animator.time.sleep", return_value=None)
def test_animate_table_rows(
    mock_sleep,
    mock_live,
):
    table = Table()
    table.add_column("Name")
    table.add_column("Status")

    rows = [
        ["FastAPI", "PASS"],
        ["Redis", "PASS"],
    ]

    live_instance = MagicMock()
    mock_live.return_value.__enter__.return_value = live_instance

    animate_table_rows(table, rows)

    assert live_instance.update.call_count == len(rows)


@patch("panoptis.cli.animator.cinematic_logo_reveal")
def test_glitch_ascii_art(mock_reveal):
    art = [
        "AAA",
        "BBB",
    ]

    glitch_ascii_art(art)

    mock_reveal.assert_called_once_with(art)


@patch("panoptis.cli.animator.Live")
@patch("panoptis.cli.animator.time.sleep", return_value=None)
@patch("panoptis.cli.animator.console.print")
def test_print_systematic_menu(
    mock_print,
    mock_sleep,
    mock_live,
):
    live_instance = MagicMock()
    mock_live.return_value.__enter__.return_value = live_instance

    print_systematic_menu()

    assert mock_print.called
    assert live_instance.update.called
