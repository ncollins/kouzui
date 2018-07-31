# encode and decode the Bittorrent serialization format
# http://www.bittorrent.org/beps/bep_0003.html

import collections
import io

def parse_string_length(s, i=''):
    # Take string from the front and return the rest
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be ':' here
    if c == b':':
        return int(i)
    else:
        raise Exception("String length should be terminated by ':'")

def parse_string(s, n):
    return s.read(n)

def parse_int(s):
    i = b''
    c = s.read(1)
    while c.isdigit():
        i += c
        c = s.read(1)
    # c should be 'e' here
    if c == b'e':
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
    d = collections.OrderedDict()
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
    elif c == b'i':
        return parse_int(s)
    elif c == b'l':
        return parse_list(s)
    elif c == b'd':
        return parse_dict(s)
    elif c == b'e' or c == '':
        None
    else:
        raise Exception("Expected a digit, 'i', 'l', or 'd'. Got {}".format(c))

#def parse(s):
#    stream = io.StringIO(s)
#    return parse_value(stream)

#print(parse('l8:abcdefgh4:spamdi10e11:abcdefghijke'))

#def extract_info(s):
#    c = s.read(1)
#    if c != b'd':
#        raise Exception("Need a dictionary to get 'info' string")
#    d = dict()
#    while True:
#        k = parse_value(s)
#        if k == b'info':
#            return parse_value(s)
#        else:
#            _ = parse_value(s)
#    raise Exception("No 'info' key found")

def encode_bytes(s):
    return b'%d:%s' % (len(s), s)

def encode_string(s):
    return encode_bytes(s.encode())

def encode_int(i):
    return b'i%de' % i

def encode_list(l):
    inner = b''.join(encode_value(v) for v in l)
    return b'l%se' % inner

def encode_dict(d):
    inner = b''.join(encode_value(k) + encode_value(v) for k,v in d.items())
    return b'd%se' % inner

def encode_value(v):
    if isinstance(v, bytes):
        return encode_bytes(v)
    elif isinstance(v, str):
        return encode_string(v)
    elif isinstance(v, int):
        return encode_int(v)
    elif isinstance(v, list):
        return encode_list(v)
    elif isinstance(v, dict):
        return encode_dict(v)
    else:
        raise Exception('Unsupported type for bencoding: "{}"'.format(type(v)))

def parse_compact_peers(raw_bytes):
    if (len(raw_bytes) % 6) != 0:
        raise Exception('Peer list length is not a multiple of 6.')
    else:
        peers = []
        for i in range(len(raw_bytes) // 6):
            ip = '.'.join(str(i) for i in raw_bytes[i:i+4])
            port = int.from_bytes(raw_bytes[i+4:i+6], byteorder='big')
            peers.append((ip, port))
        return peers
