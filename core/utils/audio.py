################################################################################
## Play Sound

from pygame import mixer


def play(filepath):
    """
    Play audio file.
    """

    mixer.init()
    mixer.music.load(filepath)
    mixer.music.play()
