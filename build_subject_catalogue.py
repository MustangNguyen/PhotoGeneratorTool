#!/usr/bin/env python3
"""Build subject-catalogue.json: concrete main subjects for each planner theme (studio/themes.py).

pick_seeds takes the least-used open subject of a slot's theme, so the catalogue decides
what the planner draws; Final only sets palette and style.
Runs again safely: existing subjects are kept and only the missing count is requested.
Uses the Codex CLI login like the app, one theme per worker.
"""
import argparse
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from studio import diversity
from studio.providers import CodexProvider
from studio.store import Store

ROOT = Path(__file__).resolve().parent
CATALOGUE_PATH = ROOT / 'subject-catalogue.json'
SCHEMA = {
    'type': 'object', 'properties': {'subjects': {'type': 'array', 'items': {
        'type': 'object', 'properties': {
            'subject': {'type': 'string', 'minLength': 1, 'maxLength': 40},
            'vi': {'type': 'string', 'minLength': 1, 'maxLength': 60},
            'family': {'type': 'string', 'maxLength': 40},
        },
        'required': ['subject', 'vi', 'family'], 'additionalProperties': False,
    }}}, 'required': ['subjects'], 'additionalProperties': False,
}
THEMES = {
    'hobby': 'hobby benches and craft workshops mid-project: woodcarving, bookbinding, model ships, stained glass, looms, watch repair, calligraphy, terrariums (no kitchen still life)',
    'music': 'musical instruments as the hero (harp, cello, accordion, gamelan, drum kit, pipe organ, sitar), practice rooms, bandstands, instrument workshops, listening corners',
    'toys': 'toys, games and models: toy train layouts, dollhouses, kites, board games mid-play, puppet theatres, marble runs, spinning tops, rocking horses',
    'festival': 'festival decorations and setups without people: lantern festivals, harvest fair stalls (not food-centric), Christmas tree farms, altars, kite festivals, parked carnival floats',
    'nature': 'mountains, forests, rivers, waterfalls, gorges, meadows and lakes, real places and generic landscapes, clear water and no mud',
    'wildlife': 'wild birds, insects, reptiles, amphibians, forest mammals, coral reef and tide-pool life, aquariums (not pets)',
    'farm': 'farms, orchards and countryside: barns, beehives, orchards at harvest, haystacks, sheep pens, vineyards, seedling greenhouses, friendly farm machinery',
    'park': 'parks and play places: carousels, ferris wheels, playgrounds, park follies, mini golf, splash pads, bandstands, boating ponds (no zoo animals)',
    'discovery': 'discovery and natural science: globes, maps, inviting natural history cabinets, fossil digs, insect study desks, observatory domes, weather stations, botanical illustration studios',
    'outdoor': 'sport, camping and outdoor gear and places without people: tents, climbing gear, kayaks, sailing dinghies, ski huts, tennis or croquet lawns, fishing piers, trailheads, surf shacks',
    'books': 'libraries, bookshops and stationery: reading nooks in public places, stationery shops, printing presses, map shops, warm archives',
    'structure': 'remarkable structures: bridges, windmills, watermills, castles, treehouses, clock towers, follies, aqueducts, stilt houses, covered bridges',
}
# One focus per round: a long EXCLUDED list alone made the model repeat itself or stop.
SUBTOPICS = {
    'hobby': ['woodworking and carving', 'paper crafts and bookbinding', 'model making', 'glass and ceramics crafts', 'textile crafts and weaving',
              'repair and tinkering benches', 'painting and calligraphy', 'plant and terrarium hobbies', 'jewellery and metal crafts', 'leather and sewing crafts'],
    'music': ['string instruments', 'wind and brass instruments', 'percussion instruments', 'keyboard instruments', 'folk and world instruments',
              'music rooms and stages', 'instrument workshops', 'listening and recording gear'],
    'toys': ['train sets and vehicles', 'dolls and dollhouses', 'outdoor toys and kites', 'board and table games', 'puppets and theatres',
             'construction and marble toys', 'spinning and mechanical toys', 'ride-on and rocking toys', 'models and miniatures'],
    'festival': ['lantern and light festivals', 'harvest and fair setups', 'winter holiday decorations', 'spring and colour festivals',
                 'altars and memorial decorations', 'kite and balloon festivals', 'parades and carnival floats', 'fireworks and boat festivals'],
    'nature': ['mountains and peaks', 'forests and woods', 'rivers and streams', 'waterfalls and gorges', 'lakes and ponds',
               'meadows and valleys', 'caves and rock formations', 'famous natural landmarks'],
    'wildlife': ['garden and forest birds', 'water birds', 'butterflies and friendly insects', 'reptiles and amphibians', 'forest mammals',
                 'grassland and mountain animals', 'coral reef life', 'tide-pool and shore life', 'aquarium fish'],
    'farm': ['barns and farm buildings', 'beekeeping', 'orchards and fruit harvest', 'field crops and haystacks', 'farm animals in pens',
             'vineyards', 'greenhouses and seedlings', 'friendly farm machinery', 'windmill and water features'],
    'park': ['fairground rides', 'playground equipment', 'park follies and pavilions', 'mini golf and games', 'water play and ponds',
             'bandstands and stages', 'botanical park features', 'park paths and bridges'],
    'discovery': ['globes and maps', 'natural history cabinets', 'fossils and geology', 'insect and plant study', 'astronomy places',
                  'weather and measuring instruments', 'exploration gear', 'botanical illustration and specimens'],
    'outdoor': ['camping gear and tents', 'climbing and hiking gear', 'paddle and sail boats', 'winter sport huts and gear',
                'lawn and court sports', 'fishing places and gear', 'surf and beach sport', 'cycling and trail places'],
    'books': ['library types', 'bookshop types', 'reading nooks', 'stationery and writing items', 'printing and binding equipment',
              'map and archive rooms', 'book furniture and ladders'],
    'structure': ['bridges', 'windmills and watermills', 'castles and forts', 'treehouses and stilt houses', 'towers and clock towers',
                  'follies and pavilions', 'aqueducts and viaducts', 'lighthouses and beacons'],
}
PROMPT = '''Return only the requested JSON. Text-only task; do not browse, run commands or modify files.

We make bright, cosy, near-photoreal jigsaw puzzle pictures without people. List {count} NEW main
subjects for the theme "{theme}": {description}.
Focus this round on: {focus}.

Rules for each subject:
- subject: generic English singular noun, 1-3 words, lowercase, no adjectives, colours, brands or
  setting (e.g. "harp", "rocking horse", "covered bridge", "kingfisher", "beehive").
- concrete, real and instantly recognisable; can be the clear hero of a pleasant picture with no people.
- big enough to be the hero of a medium shot: not a small part or component (pedal, carburetor, door casing),
  sachet, packet or utility infrastructure (drain, spillway, yard).
- no disease, danger (venomous or predatory threat), industry, weapons, alcohol, mud, insects that disgust,
  religious objects or rituals, or text-heavy items.
- every subject different from the others and from the EXCLUDED list. The LAST word is the identity:
  "iced tea" counts as "tea" and "red squirrel" as "squirrel", so never end with a last word already in EXCLUDED.
- vi: short Vietnamese name.
- family: one id from FAMILIES when it fits, else "".

FAMILIES (id: label)
{families}

EXCLUDED
{excluded}

JSON shape: {{"subjects":[{{"subject":"harp","vi":"đàn hạc","family":""}}, ...]}}'''
CLEAN_SCHEMA = {
    'type': 'object', 'properties': {'drop': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'index': {'type': 'integer'}, 'reason': {'type': 'string', 'maxLength': 80}},
        'required': ['index', 'reason'], 'additionalProperties': False,
    }}}, 'required': ['drop'], 'additionalProperties': False,
}
CLEAN_PROMPT = '''Return only the requested JSON. Text-only task; do not browse, run commands or modify files.

We make bright, cosy, near-photoreal jigsaw puzzle pictures without people. Each line is a candidate
main subject for the theme "{theme}": {description}. Drop a line when ANY of these holds:
- it does not belong to the theme (e.g. a pet in the wildlife theme, a food dish in the festival theme);
- too small or a mere part/component to be the hero of a medium shot (pedal, carburetor, eraser, sachet, door casing);
- utility, industrial or bleak (drain, spillway, workhouse, railway yard, freezer);
- dangerous, venomous or scary, alcohol, weapons, disease, mud, disgusting creatures, religious objects or rituals;
- obscure: most adults would not recognise it from a picture, or it is not a real thing;
- it repeats another line in this list under another name.
Keep everything else; most lines should be kept.

{lines}

JSON shape: {{"drop":[{{"index":3,"reason":"too small"}}, ...]}}'''


def load():
    if CATALOGUE_PATH.is_file():
        return json.loads(CATALOGUE_PATH.read_text(encoding='utf-8'))
    return {'version': 1, 'themes': {theme: [] for theme in THEMES}}


def save(raw):
    lines = []
    for theme, entries in raw['themes'].items():
        body = ',\n'.join('    ' + json.dumps(entry, ensure_ascii=False, separators=(',', ':')) for entry in entries)
        lines.append(f'  {json.dumps(theme)}: [\n{body}\n  ]')
    rejected = json.dumps(raw.get('rejected', {}), ensure_ascii=False, separators=(',', ':'))
    note = f'"note": {json.dumps(raw["note"], ensure_ascii=False)},\n' if raw.get('note') else ''
    text = '{"version": 1, ' + note + '"themes": {\n' + ',\n'.join(lines) + '\n},\n"rejected": ' + rejected + '\n}\n'
    temporary = CATALOGUE_PATH.with_suffix('.json.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(CATALOGUE_PATH)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=int, default=500)
    parser.add_argument('--chunk', type=int, default=150)
    parser.add_argument('--rounds', type=int, default=12)
    parser.add_argument('--clean', action='store_true', help='drop unsuitable subjects instead of adding new ones')
    args = parser.parse_args()
    data = ROOT / 'data'
    settings = Store(data).settings()
    raw = load()
    for theme in THEMES:
        raw['themes'].setdefault(theme, [])
        raw.setdefault('rejected', {}).setdefault(theme, [])
    labels = diversity._load_families()['labels']
    family_text = '; '.join(f'{family_id}: {label}' for family_id, label in labels.items())
    lock = threading.Lock()
    work = data / 'catalogue-building'

    def words_of(subject):
        return tuple(diversity._subject_words(subject))[-3:]

    def known_words():
        kept = [entry['subject'] for entries in raw['themes'].values() for entry in entries]
        dropped = [subject for subjects in raw['rejected'].values() for subject in subjects]
        return [words_of(subject) for subject in kept + dropped]

    def clean(theme):
        """Review unchecked subjects in chunks; dropped ones are remembered so they are not suggested again."""
        provider = CodexProvider(settings, work)
        while True:
            with lock:
                todo = [entry for entry in raw['themes'][theme] if not entry.get('checked')][:args.chunk]
            if not todo:
                return
            lines = '\n'.join(f'{n}: {entry["subject"]}' for n, entry in enumerate(todo))
            directory = Path(tempfile.mkdtemp(prefix=f'clean-{theme}-', dir=provider.work_root))
            try:
                reply = json.loads(provider._exec(CLEAN_PROMPT.format(theme=theme, description=THEMES[theme], lines=lines),
                                                  directory, structured=True, timeout=900, output_schema=CLEAN_SCHEMA))
            except Exception as error:
                print(f'{theme}: clean failed: {error}', flush=True)
                return
            drop = {entry['index'] for entry in reply.get('drop', []) if isinstance(entry.get('index'), int)}
            with lock:
                for n, entry in enumerate(todo):
                    if n in drop:
                        raw['themes'][theme].remove(entry)
                        raw['rejected'][theme].append(entry['subject'])
                    else:
                        entry['checked'] = True
                save(raw)
                total = len(raw['themes'][theme])
            print(f'{theme}: dropped {len(drop)}/{len(todo)} -> {total}', flush=True)

    def build(theme):
        provider = CodexProvider(settings, work)
        for round_number in range(args.rounds):
            with lock:
                entries = raw['themes'][theme]
                missing = args.target - len(entries)
                excluded = ', '.join([entry['subject'] for entry in entries] + raw['rejected'][theme])
            if missing <= 0:
                break
            topics = SUBTOPICS[theme]
            prompt = PROMPT.format(count=min(args.chunk, missing + 20, 60), theme=theme, description=THEMES[theme],
                                   focus=topics[round_number % len(topics)],
                                   families=family_text, excluded=excluded or '(none)')
            directory = Path(tempfile.mkdtemp(prefix=f'{theme}-', dir=provider.work_root))
            try:
                reply = json.loads(provider._exec(prompt, directory, structured=True, timeout=900, output_schema=SCHEMA))
            except Exception as error:
                print(f'{theme}: call failed: {error}', flush=True)
                continue
            added = 0
            with lock:
                seen = known_words()
                for item in reply.get('subjects', []):
                    subject = ' '.join(str(item.get('subject', '')).lower().split())
                    words = words_of(subject)
                    if not words or not all(word.isalpha() for word in words):
                        continue
                    if any(diversity._same_subject(words, other) for other in seen):
                        continue
                    family = diversity._family_of(words)
                    claimed = str(item.get('family', '')).strip()
                    if family is None and claimed in labels:
                        family = claimed
                    entry = {'subject': ' '.join(words), 'vi': ' '.join(str(item.get('vi', '')).split())[:60]}
                    if family:
                        entry['family'] = family
                    raw['themes'][theme].append(entry)
                    seen.append(words)
                    added += 1
                save(raw)
                total = len(raw['themes'][theme])
            print(f'{theme}: +{added} -> {total}', flush=True)

    with ThreadPoolExecutor(max_workers=len(THEMES)) as pool:
        for future in [pool.submit(clean if args.clean else build, theme) for theme in THEMES]:
            future.result()
    print({theme: len(entries) for theme, entries in raw['themes'].items()})


if __name__ == '__main__':
    main()
