#!/usr/bin/env python3
"""Build subject-catalogue.json: about 500 concrete main subjects for each Final theme.

Final only names ~630 distinct subjects, so seeds ran dry after a few batches and the
planner fell back to its favourites. The catalogue gives pick_seeds a much wider pool.
Runs again safely: existing subjects are kept and only the missing count is requested.
Uses the Codex CLI login like the app, one theme per worker.
"""
import argparse
import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from studio import diversity, final
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
    'food': 'dishes, baked goods, desserts, ingredients and table spreads from many world cuisines',
    'interior': 'rooms, room corners, furniture, fixtures and home features in cosy interiors',
    'garden': 'flowers, shrubs, trees, fruit and vegetable plants, garden structures and fresh nature spots',
    'objects': 'nostalgic objects, collectibles, crafts, toys, instruments and keepsakes',
    'facade': 'buildings, facades, streets, bridges, towers, landmarks types and townscapes',
    'animal': 'cute animals: pets, farm animals, birds, wildlife and sea creatures shown in natural habitats',
    'vehicle': 'vintage and travel vehicles: cars, bikes, trains, trams, boats, aircraft, carts and stations',
    'shop': 'kinds of small shops, market stalls, kiosks, counters and street vendor carts (the shop itself, not a product it sells)',
    'coast': 'seaside and holiday subjects: beaches, harbours, piers, lighthouses, beach huts and coastal scenes',
    'drink': 'drinks and tea or coffee settings: teas, coffees, juices, smoothies, mocktails, hot chocolate, drink stands',
}
# One focus per round: a long EXCLUDED list alone made the model repeat itself or stop.
SUBTOPICS = {
    'food': ['European dishes', 'Asian dishes', 'Latin American and African dishes', 'Middle Eastern and South Asian dishes',
             'breads and savoury bakes', 'cakes, pies and tarts', 'sweets, candies and small desserts', 'fruits and nuts as food',
             'vegetables, herbs and pantry ingredients', 'breakfast and picnic foods', 'soups, stews and preserves', 'cheeses, cured foods and seafood'],
    'interior': ['rooms and room types', 'seating and beds', 'tables, desks and storage furniture', 'kitchen fittings and appliances',
                 'lighting and fireplaces', 'windows, doors, stairs and architectural details', 'textiles and soft furnishings',
                 'bathroom and laundry fixtures', 'hobby, study and play corners', 'decorative home features'],
    'garden': ['garden flowers', 'wildflowers and meadow plants', 'flowering shrubs and climbers', 'trees and fruit trees',
               'vegetable and herb plants', 'succulents, ferns and foliage plants', 'garden structures and features',
               'water and rock garden features', 'orchard and berry plants', 'tropical and exotic plants'],
    'objects': ['toys and games', 'musical instruments', 'clocks, cameras and old devices', 'stationery and writing items',
                'sewing, knitting and craft supplies', 'collectibles and souvenirs', 'kitchenware and tableware antiques',
                'travel items and luggage', 'sports and outdoor gear', 'decorative ornaments and keepsakes'],
    'facade': ['house types', 'civic and public buildings', 'religious and historic building types', 'towers, gates and walls',
               'bridges and waterside structures', 'shopfront and street features', 'rural and farm buildings', 'garden and park structures',
               'mountain and countryside buildings', 'squares, stairs and street spaces'],
    'animal': ['pets', 'farm animals', 'garden birds', 'water birds and seabirds', 'forest mammals', 'savanna and grassland animals',
               'mountain and polar animals', 'rainforest animals', 'reptiles, amphibians and pond life', 'sea creatures', 'baby animals',
               'friendly insects and small creatures like butterflies and ladybirds'],
    'vehicle': ['vintage cars', 'trucks, vans and utility vehicles', 'bicycles, scooters and motorbikes', 'trains, trams and rail',
                'boats and ships', 'aircraft and balloons', 'carts, carriages and wagons', 'stations, ports and travel places',
                'camping and caravan vehicles', 'toy-like and novelty vehicles'],
    'shop': ['food shops and bakeries', 'market stalls', 'cafes and food kiosks', 'craft and gift shops', 'flower and garden shops',
             'book, music and stationery shops', 'clothing and accessory shops', 'household and hardware shops', 'street vendor carts',
             'service shops like barbers or repair (no people)'],
    'coast': ['beach features', 'harbour and pier structures', 'seaside buildings', 'boats on the coast', 'beach gear and toys',
              'rock pools and shore life', 'coastal plants and landscapes', 'seaside food and leisure spots', 'islands and lagoons',
              'lighthouses and coastal landmarks'],
    'drink': ['teas of the world', 'coffee drinks', 'juices and smoothies', 'milk drinks and hot chocolate', 'sodas, lemonades and mocktails',
              'traditional and regional drinks', 'teaware and coffee equipment', 'drink stands, bars and serving vessels',
              'fermented and sparkling soft drinks', 'frozen and iced drinks'],
}
PROMPT = '''Return only the requested JSON. Text-only task; do not browse, run commands or modify files.

We make bright, cosy, near-photoreal jigsaw puzzle pictures without people. List {count} NEW main
subjects for the theme "{theme}": {description}.
Focus this round on: {focus}.

Rules for each subject:
- subject: generic English singular noun, 1-3 words, lowercase, no adjectives, colours, brands or
  setting (e.g. "paella", "rocking chair", "lighthouse", "hedgehog", "tram").
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

JSON shape: {{"subjects":[{{"subject":"paella","vi":"cơm paella","family":"rice_dish"}}, ...]}}'''
CLEAN_SCHEMA = {
    'type': 'object', 'properties': {'drop': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'index': {'type': 'integer'}, 'reason': {'type': 'string', 'maxLength': 80}},
        'required': ['index', 'reason'], 'additionalProperties': False,
    }}}, 'required': ['drop'], 'additionalProperties': False,
}
CLEAN_PROMPT = '''Return only the requested JSON. Text-only task; do not browse, run commands or modify files.

We make bright, cosy, near-photoreal jigsaw puzzle pictures without people. Each line is a candidate
main subject for the theme "{theme}": {description}. Drop a line when ANY of these holds:
- it does not belong to the theme (e.g. a product such as "jacket" in a shop theme, a room in an objects theme);
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
    text = '{"version": 1, "themes": {\n' + ',\n'.join(lines) + '\n},\n"rejected": ' + rejected + '\n}\n'
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
