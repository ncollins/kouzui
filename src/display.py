import math
import os
import shutil

CGREEN  = '\33[32m'
CBLUE   = '\33[34m'
CBEIGE  = '\33[36m'
CWHITE  = '\33[37m'
CEND = '\033[0m'

def display_piece(count, display_block):
    if os.name == 'windows':
        if count == display_block:
            return "X"
        elif (count / display_block) > 0.66:
            return "x"
        elif (count / display_block) > 0.33:
            return ":"
        elif (count / display_block) > 0:
            return "."
        else:
            return " "
    else:
        block = '\u25A9'
        if count == display_block:
            return CGREEN + block + CEND
        elif (count / display_block) > 0.66:
            return CBLUE + block + CEND
        elif (count / display_block) > 0.33:
            return CBEIGE + block + CEND
        elif (count / display_block) > 0:
            return CWHITE + block + CEND
        else:
            return " "

MAX_TEXT_LENGTH = 35

def pretty_print(width, p_id, pieces, received_from, sent_to):
    lines = [
    p_id.decode('ascii'),
    'Complete      : {}%'.format(math.floor(sum(pieces)/len(pieces) * 100)),
    'Received from : {} blocks'.format(received_from) if (received_from is not None) else '',
    'Sent to       : {} blocks'.format(sent_to) if (sent_to is not None) else ''
            ]
    ##
    #MAX_TEXT_LENGTH = max(map(len,lines))
    grid_width = width - (MAX_TEXT_LENGTH + 2)
    total_grid_count = grid_width * len(lines)
    ##
    num_pieces = len(pieces)
    display_block = num_pieces // total_grid_count
    count_pieces = [
            sum(pieces[i*display_block:(i+1)*display_block])
            for i in range(total_grid_count)
    ]
    display_pieces = [display_piece(n, display_block) for n in count_pieces]
    ##
    print(width * '-')
    for i in range(len(lines)):
        text = lines[i][:MAX_TEXT_LENGTH]
        spaces = (MAX_TEXT_LENGTH - len(text) + 1) * ' '
        grid = ''.join(display_pieces[i*grid_width:(i+1)*grid_width])
        line = text + spaces + grid
        print(line)

def print_peers(torrent, peers):
    terminal_info = shutil.get_terminal_size()
    width = terminal_info.columns
    print(chr(27) + "[2J")
    #pretty_print(width, torrent.peer_id + b' (self)', torrent._complete, "not applicable", "not applicable") 
    pretty_print(width, torrent.peer_id + b' (self)', torrent._complete, None, None) 
    for _, p_state in sorted(peers.items())[:4]:
        pretty_print(width, p_state.peer_id, p_state.get_pieces(), p_state._total_download_count, p_state._total_upload_count)
