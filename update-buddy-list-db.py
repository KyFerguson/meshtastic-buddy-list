#!/usr/bin/python
# -*- coding:utf-8 -*-
import sys
import os

import logging
# from waveshare_epd import epd2in7  # Commented out for testing without hardware
import time
from PIL import Image, ImageDraw, ImageFont
import traceback
import json
from datetime import datetime, timedelta
import sqlite3

logging.basicConfig(level=logging.WARNING)
always_update = 1  # set to zero for normal operation, 1 for development to make it update every time
font_size = 13
max_list_len = 14
char_limit = 20
node_block_list = []
# the node block list lets you omit certain nodes from your list. I put all my own nodes here.
file_path = "/home/ky/Documents/Projects/meshtastic-buddy-list/"
db_path = "/home/ky/Documents/Projects/meshtastic-buddy-list/buddy_list.db"

# Define time ranges
now = datetime.now()
active_ago = now - timedelta(minutes=17)
twenty_four_hours_ago = now - timedelta(hours=24)
one_week_ago = now - timedelta(weeks=1)

# Lists to store nodes based on last seen time
last_active = []
last_24_hours = []
last_1_week = []


def init_database():
    """Initialize the database with required tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create nodes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            long_name TEXT NOT NULL,
            first_heard TEXT NOT NULL,
            last_heard TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # Create times_heard table for storing all heard times
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS times_heard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT NOT NULL,
            heard_at TEXT NOT NULL,
            FOREIGN KEY (node_id) REFERENCES nodes(node_id)
        )
    ''')

    # Create change_log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            change_type TEXT NOT NULL,
            node_id TEXT,
            description TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()


def load_nodes_from_db():
    """Load all nodes from the database into the same format as JSON."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('SELECT node_id, long_name FROM nodes')
    nodes = {}

    for node_id, long_name in cursor.fetchall():
        cursor.execute('SELECT heard_at FROM times_heard WHERE node_id = ? ORDER BY heard_at', (node_id,))
        times_heard = [row[0] for row in cursor.fetchall()]

        nodes[node_id] = {
            'Long Name': long_name,
            'Times Heard': times_heard
        }

    conn.close()
    return nodes


def save_node_to_db(node_id, node_data):
    """Save or update a node in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    times_heard = node_data.get('Times Heard', [])
    if not times_heard:
        conn.close()
        return

    first_heard = times_heard[0]
    last_heard = times_heard[-1]
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Insert or update node
    cursor.execute('''
        INSERT OR REPLACE INTO nodes (node_id, long_name, first_heard, last_heard, updated_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (node_id, node_data['Long Name'], first_heard, last_heard, updated_at))

    # Delete old times_heard entries for this node
    cursor.execute('DELETE FROM times_heard WHERE node_id = ?', (node_id,))

    # Insert all times_heard
    for heard_at in times_heard:
        cursor.execute('INSERT INTO times_heard (node_id, heard_at) VALUES (?, ?)',
                      (node_id, heard_at))

    conn.commit()
    conn.close()


def log_change_to_db(change_type, node_id, description):
    """Log a change to the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        INSERT INTO change_log (timestamp, change_type, node_id, description)
        VALUES (?, ?, ?, ?)
    ''', (timestamp, change_type, node_id, description))

    conn.commit()
    conn.close()


def categorize_nodes(node_list):
    for node_id, node_data in node_list.items():
        # Extract the most recent time and the first time the node was heard
        times_heard = node_data.get('Times Heard', [])

        if not times_heard:
            continue  # Skip if no Times Heard data

        # Convert the most recent and first "Times Heard" entries to datetime objects
        last_heard_str = times_heard[-1]
        last_heard_time = datetime.strptime(last_heard_str, "%Y-%m-%d %H:%M:%S")

        first_heard_str = times_heard[0]
        first_heard_time = datetime.strptime(first_heard_str, "%Y-%m-%d %H:%M:%S")

        # If the node name is in the block list, don't add it to any of these lists
        if node_data['Long Name'] in node_block_list:
            continue

        # Check if the device is new (first seen within the last week)
        if first_heard_time > twenty_four_hours_ago:
            # If the node was first seen within the last 24 hours, add two asterisks
            long_name = f"**{node_data['Long Name']}"
        elif first_heard_time > one_week_ago:
            # If the node was first seen within the last week but more than 24 hours ago, add one asterisk
            long_name = f"*{node_data['Long Name']}"
        else:
            # If the node is older than one week, no asterisk
            long_name = node_data['Long Name']

        # Check which time range the last heard time falls into
        if last_heard_time > active_ago:
            last_active.append(long_name)
        elif active_ago >= last_heard_time > twenty_four_hours_ago:
            last_24_hours.append(long_name)
        elif twenty_four_hours_ago >= last_heard_time > one_week_ago:
            last_1_week.append(long_name)

    last_active.sort()
    last_24_hours.sort()
    last_1_week.sort()


def load_full_list_from_file(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r') as file:
            return [line.strip() for line in file.readlines()]
    return []


def save_full_list_to_file(filepath, full_list):
    with open(filepath, 'w') as file:
        for item in full_list:
            file.write(item + "\n")


def log_node_changes(old_node_list, new_node_list):
    """Log any changes, additions, or deletions to nodes to the database."""
    changes = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check for new nodes
    for node_id in new_node_list:
        if node_id not in old_node_list:
            node_data = new_node_list[node_id]
            long_name = node_data.get('Long Name', 'Unknown')
            description = f"NEW NODE: {long_name} (ID: {node_id})"
            log_change_to_db('NEW', node_id, description)
            changes.append(f"[{timestamp}] {description}")

    # Check for deleted nodes
    for node_id in old_node_list:
        if node_id not in new_node_list:
            node_data = old_node_list[node_id]
            long_name = node_data.get('Long Name', 'Unknown')
            description = f"DELETED NODE: {long_name} (ID: {node_id})"
            log_change_to_db('DELETED', node_id, description)
            changes.append(f"[{timestamp}] {description}")

    # Check for changes to existing nodes
    for node_id in new_node_list:
        if node_id in old_node_list:
            old_data = old_node_list[node_id]
            new_data = new_node_list[node_id]

            # Check if Long Name changed
            if old_data.get('Long Name') != new_data.get('Long Name'):
                description = f"CHANGED: {old_data.get('Long Name', 'Unknown')} -> {new_data.get('Long Name', 'Unknown')} (ID: {node_id})"
                log_change_to_db('CHANGED', node_id, description)
                changes.append(f"[{timestamp}] {description}")

            # Check if Times Heard list grew (new activity)
            old_times = old_data.get('Times Heard', [])
            new_times = new_data.get('Times Heard', [])
            if len(new_times) > len(old_times):
                new_entries = len(new_times) - len(old_times)
                long_name = new_data.get('Long Name', 'Unknown')
                description = f"ACTIVITY: {long_name} - {new_entries} new message(s)"
                log_change_to_db('ACTIVITY', node_id, description)
                changes.append(f"[{timestamp}] {description}")

    if changes:
        print(f"{timestamp}: Logged {len(changes)} change(s)")


def main():
    try:
        # Initialize the database
        init_database()

        # Load the previous node list from the database
        old_node_list = load_nodes_from_db()

        # Load the current sample-node-archive-updated.json file
        with open(file_path + 'sample-node-archive-updated.json', 'r') as file:
            node_list = json.load(file)

        # Save all nodes to the database
        for node_id, node_data in node_list.items():
            save_node_to_db(node_id, node_data)

        # Log any changes between old and new node lists
        log_node_changes(old_node_list, node_list)

        # Categorize the nodes based on their last heard time
        categorize_nodes(node_list)

        # These are the titles for each section
        active_name = "ACTIVE"
        hours24_name = "TODAY"
        week1_name = "THIS WEEK"

        # This creates one list that is all of the entries
        full_list = []
        if last_active != []:
            full_list.append(active_name)
            for entry in last_active:
                full_list.append(entry[0:char_limit])
        if last_24_hours != []:
            if full_list != []:
                full_list.append("")
            full_list.append(hours24_name)
            for entry in last_24_hours:
                full_list.append(entry[0:char_limit])
        if last_1_week != []:
            if full_list != []:
                full_list.append("")
            full_list.append(week1_name)
            for entry in last_1_week:
                full_list.append(entry[0:char_limit])

        saved_full_list = load_full_list_from_file(file_path + 'full_list.txt')
        # save it so you don't have to update next time if nothing has changed.

        if saved_full_list != full_list or always_update == 1:
            print(datetime.now(), 'different list than before, updating')
            print("Display list:", full_list)
            # Skipping e-paper display code for testing
            """
            logging.info("epd2in7 Demo")
            epd = epd2in7.EPD()

            '''2Gray(Black and white) display'''
            logging.info("init and Clear")
            epd.init()
            epd.Clear(0xFF)

            my_font = ImageFont.truetype(file_path + 'waveshare_epd/NotoSansUI-Regular.ttf', font_size)
            my_font_bold = ImageFont.truetype(file_path + 'waveshare_epd/NotoSansUI-Bold.ttf', font_size)

            Himage = Image.new('1', (epd.height, epd.width), 255)  # 255: clear the frame
            draw = ImageDraw.Draw(Himage)

            text_bbox = my_font.getbbox('Active')
            text_size = (text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1])

            spacing = 2

            # yes there is probably a way better way of doing this but I did this project piece by piece and didn't
            # want to rewrite the old stuff so here we are ¯\_(ツ)_/¯

            if len(full_list) > max_list_len:
                # if the list will be two columns
                if full_list[max_list_len - 1] == hours24_name or full_list[max_list_len - 1] == week1_name:
                    # if a heading falls on the last entry in a column, shorten the first column by one and put the rest on the second column
                    starting_y = 0
                    for entry in full_list[0:max_list_len - 1]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((4, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((4, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing

                    starting_y = 0
                    for entry in full_list[max_list_len - 1:]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((132, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((132, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing

                elif full_list[max_list_len] == "":
                    # if the first entry on the second column is a blank space, eliminate the space by starting the second column one entry later
                    starting_y = 0
                    for entry in full_list[0:max_list_len]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((4, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((4, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing

                    starting_y = 0
                    for entry in full_list[max_list_len + 1:]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((132, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((132, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                else:
                    # if there are no weird circumstances, do this
                    starting_y = 0
                    for entry in full_list[0:max_list_len]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((4, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((4, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing

                    starting_y = 0
                    for entry in full_list[max_list_len:]:
                        if entry == hours24_name or entry == week1_name or entry == active_name:
                            draw.text((132, starting_y), entry, font=my_font_bold, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
                        else:
                            draw.text((132, starting_y), entry, font=my_font, fill=0)
                            starting_y = starting_y + text_size[1] + spacing
            else:
                # if the list is only one column
                print('not greater than', max_list_len - 1)
                starting_y = 0
                for entry in full_list:
                    if entry == hours24_name or entry == week1_name or entry == active_name:
                        draw.text((4, starting_y), entry, font=my_font_bold, fill=0)
                        starting_y = starting_y + text_size[1] + spacing
                    else:
                        draw.text((4, starting_y), entry, font=my_font, fill=0)
                        starting_y = starting_y + text_size[1] + spacing

            save_full_list_to_file(file_path + 'full_list.txt', full_list)

            epd.display(epd.getbuffer(Himage))
            time.sleep(2)

            logging.info("Goto Sleep...")
            epd.sleep()
            """
        else:
            print(datetime.now(), 'same list as before, did not update')


    except FileNotFoundError:
        print("Error: The file 'sample-node-archive-updated.json' was not found.")
    except json.JSONDecodeError:
        print("Error: Failed to decode JSON from 'sample-node-archive-updated.json'.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    main()