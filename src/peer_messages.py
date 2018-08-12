import io


#def parse(byte_string):
#    s = io.BytesIO(byte_string)
#    c = s.read(1)
#    if c == b'0':
#        return parse_choke(s)
#    elif c == b'1':
#        return parse_unchoke(s)
#    elif c == b'2':
#        return parse_interested(s)
#    elif c == b'3':
#        return parse_uninterested(s)
#    elif c == b'4':
#        return parse_have(s)
#    elif c == b'5':
#        return parse_bitfield(s)
#    elif c == b'6':
#        return parse_request(s)
#    elif c == b'7':
#        return parse_piece(s)
#    elif c == b'8':
#        return parse_cancel(s)
