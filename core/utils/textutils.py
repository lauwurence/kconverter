################################################################################
## Utils Text

def format_size(size):

    if size < 1024 * 1024:
        return f'{size / 1024:.0f} KB'

    if size < 1024 * 1024 * 1024:
        return f'{size / 1024 / 1024:.1f} MB'

    return f'{size / 1024 / 1024 / 1024:.2f} GB'


def format_eta(seconds):

    if seconds <= 0:
        return "0s"

    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes:02d}m"

    if minutes:
        return f"{minutes}m {seconds:02d}s"

    return f"{seconds}s"
