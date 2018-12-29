import copy
import itertools
from music21.note import Note
from music21.pitch import Pitch
from music21.chord import Chord
from music21.roman import RomanNumeral
from music21.key import Key
from music21.clef import BassClef, TrebleClef
from music21.stream import Stream, Part, Score, Measure, Voice

SOPRANO_RANGE = (Pitch('C4'), Pitch('G5'))
ALTO_RANGE = (Pitch('G3'), Pitch('C5'))
TENOR_RANGE = (Pitch('C3'), Pitch('G4'))
BASS_RANGE = (Pitch('F2'), Pitch('C4'))


def voiceNote(noteName, pitchRange):
    '''Generates voicings for a note in a given pitch range.'''
    lowerOctave = pitchRange[0].octave
    upperOctave = pitchRange[1].octave
    for octave in range(lowerOctave, upperOctave + 1):
        n = Pitch(noteName + str(octave))
        if pitchRange[0] <= n <= pitchRange[1]:
            yield n


def _voiceTriadUnordered(noteNames):
    assert len(noteNames) == 3
    for tenor, alto, soprano in itertools.permutations(noteNames, 3):
        for sopranoNote in voiceNote(soprano, SOPRANO_RANGE):
            altoMin = max((ALTO_RANGE[0], sopranoNote.transpose('-P8')))
            altoMax = min((ALTO_RANGE[1], sopranoNote))
            for altoNote in voiceNote(alto, (altoMin, altoMax)):
                tenorMin = max((TENOR_RANGE[0], altoNote.transpose('-P8')))
                tenorMax = min((TENOR_RANGE[1], altoNote))
                for tenorNote in voiceNote(tenor, (tenorMin, tenorMax)):
                    yield Chord([tenorNote, altoNote, sopranoNote])


def _voiceChord(noteNames):
    assert len(noteNames) == 4
    bass = noteNames.pop(0)
    for chord in _voiceTriadUnordered(noteNames):
        for bassNote in voiceNote(bass, BASS_RANGE):
            if bassNote <= chord.bass():
                chord4 = copy.deepcopy(chord)
                chord4.add(bassNote)
                yield chord4


def voiceChord(chord):
    '''Generates four-part voicings for a fifth or seventh chord.

    The bass note is kept intact, though other notes (and doublings) are
    allowed to vary between different voicings. Intervals between adjacent
    non-bass parts are limited to a single octave.
    '''
    noteNames = [pitch.name for pitch in chord.pitches]
    if chord.containsSeventh():
        yield from _voiceChord(noteNames)
    elif chord.inversion() == 2:
        noteNames.append(noteNames[0]) # must double the fifth
        yield from _voiceChord(noteNames)
    else:
        # double the root
        yield from _voiceChord(noteNames + [chord.root().name])
        # double the third
        yield from _voiceChord(noteNames + [chord.third.name])
        # double the fifth
        yield from _voiceChord(noteNames + [chord.fifth.name])
        # option to omit the fifth
        if chord.romanNumeral == 'I' and chord.inversion() == 0:
            yield from _voiceChord(
                [chord.root().name] * 3 + [chord.third.name])


def cost(key, chord1, chord2):
    '''Function to optimize over: enforces contrary motion, etc.'''
    score = 0

    # Overlapping voices
    if (chord2[0] > chord1[1]
        or chord2[1] < chord1[0] or chord2[1] > chord1[2]
        or chord2[2] < chord1[1] or chord2[2] > chord1[3]
        or chord2[3] < chord1[2]):
        score += 50

    # Avoid big jumps
    score += (abs(chord1.pitches[0].midi - chord2.pitches[0].midi) // 2) ** 2
    score += abs(chord1.pitches[1].midi - chord2.pitches[1].midi) ** 2
    score += abs(chord1.pitches[2].midi - chord2.pitches[2].midi) ** 2
    score += abs(chord1.pitches[3].midi - chord2.pitches[3].midi) ** 2 // 10

    # Contrary motion is good, parallel fifths are bad
    for i in range(4):
        for j in range(i + 1, 4):
            t1, t2 = chord1.pitches[j], chord2.pitches[j]
            b1, b2 = chord1.pitches[i], chord2.pitches[i]
            i1, i2 = t1.midi - b1.midi, t2.midi - b2.midi
            if i1 % 12 == i2 % 12 == 7: # Parallel fifth
                score += 60
            if i1 % 12 == i2 % 12 == 0: # Parallel octave
                score += 100
            if (t2 > t1 and b2 > b1) or (t2 < t1 and b2 < b1): # Not contrary
                score += 1

    # Chordal 7th should resolve downward or stay
    if chord1.seventh:
        seventhVoice = chord1.pitches.index(chord1.seventh)
        delta = chord2.pitches[seventhVoice].midi - chord1.seventh.midi
        if delta < -2 or delta > 0:
            score += 80

    # V->I means ti->do or ti->sol
    if (chord1.root().name == key.getDominant().name
        and chord2.root().name == key.getTonic().name):
        voice = chord1.pitches.index(chord1.third)
        delta = chord2.pitches[voice].midi - chord1.third.midi
        if delta != 1 and (delta != -4 or voice == 3):
            score += 80

    return score


def voiceProgression(key, chordProgression):
    '''Voice a progression of chords using DP.'''
    key = Key(key)
    if isinstance(chordProgression, str):
        chordProgression = chordProgression.split()

    dp = [{} for _ in chordProgression]
    for i, numeral in enumerate(chordProgression):
        chord = RomanNumeral(numeral, key)
        voicings = voiceChord(chord)
        if i == 0:
            for v in voicings:
                dp[0][v] = (0, None)
        else:
            for v in voicings:
                best = (float('inf'), None)
                for pv, (pcost, _) in dp[i - 1].items():
                    ccost = pcost + cost(key, pv, v)
                    if ccost < best[0]:
                        best = (ccost, pv)
                dp[i][v] = best

    cur, (totalCost, _) = min(dp[-1].items(), key=lambda p: p[1][0])
    print('Cost:', totalCost)

    ret = []
    for i in reversed(range(len(chordProgression))):
        ret.append(cur)
        cur = dp[i][cur][1]
    return list(reversed(ret))


def showChords(chords):
    '''Displays a sequence of chords on a four-part score.

    Soprano and alto parts are displayed on the top (treble) clef, while tenor
    and bass parts are displayed on the bottom (bass) clef, with correct stem
    directions.
    '''
    voices = [Voice() for _ in range(4)]
    for chord in chords:
        bass, tenor, alto, soprano = [Note(p) for p in chord.pitches]
        bass.stemDirection = alto.stemDirection = 'down'
        tenor.stemDirection = soprano.stemDirection = 'up'
        voices[0].append(soprano)
        voices[1].append(alto)
        voices[2].append(tenor)
        voices[3].append(bass)

    female = Part([TrebleClef(), voices[0], voices[1]])
    male = Part([BassClef(), voices[2], voices[3]])
    score = Score([female, male])
    score.show()


def showVoicings(key, numeral):
    '''Displays all the valid voicings of a roman numeral in a key.'''
    chord = RomanNumeral(numeral, key)
    showChords(voiceChord(chord))


def main():
    showChords(voiceProgression('B-', 'I I6 IV V43/ii ii V V7 I'))


if __name__ == '__main__':
    main()
