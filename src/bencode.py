# encode and decode the Bittorrent serialization format
# http://www.bittorrent.org/beps/bep_0003.html

import io

def parse_string_length(s, i=''):
    # Take string from the front and return the rest
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be ':' here
    if c == ':':
        return int(i)
    else:
        raise Exception("String length should be terminated by ':'")

def parse_string(s, n):
    return s.read(n)

def parse_int(s):
    i = ''
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be 'e' here
    if c == 'e':
        return int(i)
    else:
        raise Exception("Integer not terminated by 'e'")

def parse_list(s):
    l = []
    while True:
        v = parse_value(s)
        if v is None:
            return l
        else:
            l.append(v)

def parse_dict(s):
    d = dict()
    while True:
        k = parse_value(s)
        if k is None:
            return d
        else:
            v = parse_value(s)
            d[k] = v

def parse_value(s):
    # `s` is a string stream object
    # look at first character
    # digit -> string
    # 'i'   -> integer
    # 'l'   -> list
    # 'd'   -> dictionary
    c = s.read(1)
    if c.isdigit():
        length = parse_string_length(s, c)
        return parse_string(s, length) 
    elif c == 'i':
        return parse_int(s)
    elif c == 'l':
        return parse_list(s)
    elif c == 'd':
        return parse_dict(s)
    elif c == 'e' or c == '':
        None
    else:
        raise Exception("Expected a digit, 'i', 'l', or 'd'. Got {}".format(c))

def parse(s):
    stream = io.StringIO(s)
    return parse_value(stream)

print(parse('l8:abcdefgh4:spamdi10e11:abcdefghijke'))
