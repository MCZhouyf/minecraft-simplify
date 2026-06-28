import json
from datetime import datetime
import Adam.util_info
import utils as U
from functools import cmp_to_key


def compare_keys(key1, key2):
    if len(key1) < len(key2):
        return -1
    elif len(key1) > len(key2):
        return 1
    else:
        if key1 < key2:
            return -1
        elif key1 > key2:
            return 1
        else:
            return 0


key_cmp_func = cmp_to_key(compare_keys)


def generate_next_key(current_key):
    if current_key[-1] != 'z':
        return current_key[:-1] + chr(ord(current_key[-1]) + 1)
    else:
        if current_key == 'z':
            return 'aa'
        else:
            return generate_next_key(current_key[:-1]) + 'a'


def rename_item(item: str):
    if 'log' in item:
        return 'log'
    elif 'planks' in item:
        return 'planks'
    elif 'fence_gate' in item:
        return 'fence_gate'
    elif 'fence' in item:
        return 'fence'
    else:
        return item


def rename_item_rev(item: str):
    if 'log' in item:
        return 'oak_log'
    elif 'planks' in item:
        return 'oak_planks'
    elif 'fence_gate' in item:
        return 'oak_fence_gate'
    elif 'fence' in item:
        return 'oak_fence'
    else:
        return item


def translate_item_name_to_letter(name: str):
    return Adam.util_info.material_names_rev_dict[rename_item(name)]


def translate_item_name_list_to_letter(name_list: list):
    return [translate_item_name_to_letter(item) for item in name_list]


def translate_item_letter_to_name(letter: str):
    return Adam.util_info.material_names_dict[letter]


def translate_action_name_to_letter(name: str):
    return Adam.util_info.action_names_rev_dict[name]


def translate_action_letter_to_name(letter: str):
    if letter[:4] == 'move':
        return letter
    return Adam.util_info.action_names_dict[letter]


def check_in_material(added_items: list, effect: str):
    for added_item in added_items:
        if translate_item_letter_to_name(effect) == rename_item(added_item):
            return True
    return False


def check_len_valid(materials: list):
    for item in materials:
        if len(item) > 2:
            return False
    return True


def get_inventory_number(inventory: dict, material: str):
    material_name = rename_item_rev(translate_item_letter_to_name(material))
    if material_name in ['oak_log', 'oak_planks', 'stick', 'cobblestone', 'raw_iron', 'iron_ingot', 'diamond',
                         'raw_gold', 'gold_ingot']:
        inventory[material_name] = 32
    else:
        inventory[material_name] = 1
    return inventory


def get_item_changes(start_item: dict, end_item: dict):
    consumed_items = []
    added_items = []

    for item, quantity in start_item.items():
        if item not in end_item or end_item[item] < quantity:
            consumed_items.append(item)

    for item, quantity in end_item.items():
        if item not in start_item or start_item[item] < quantity:
            added_items.append(item)

    return consumed_items, added_items


def recorder(start_item: dict, end_item: dict, consumed_items: list, added_items: list, action_type: str,
             file_path: str):
    log_json_path = U.f_join(file_path, "log_data", action_type + ".json")
    log_dict = {
        'Start item': start_item,
        'End item': end_item,
        'Action type': action_type,
        'Consumed items': consumed_items,
        'Added items': added_items,
    }

    try:
        with open(log_json_path, 'r') as file:
            try:
                logs = json.load(file)
            except json.JSONDecodeError:
                logs = []
    except FileNotFoundError:
        logs = []
    logs.append(log_dict)
    with open(log_json_path, 'w') as file:
        json.dump(logs, file, indent=4)


def get_time():
    now = datetime.now()
    return now.strftime("%Y-%m-%d-%H-%M-%S")
