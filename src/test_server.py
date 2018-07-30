# this just exists for learning trio and checking
# that my tracker request is sending the right
# things

import h11
import trio

import http_stream

async def handler(stream):
    h = http_stream.Http_stream(stream, h11.SERVER)

    # valid events are:
    # - Request
    # - InformationalResponse
    # - Response
    # - Data
    # - EndOfMessage
    # - ConnectionClosed

    request, data = await h.receive_with_data()

    response = h11.Response(status_code=200,reason=b'OK', headers=[])
    await h.send_event(response)

    body = h11.Data(data = b'Thanks for your message!!!!!')
    await h.send_event(body)
    await h.send_event(h11.EndOfMessage())
    await h.close()

async def run_server():
    print("Start server")
    await trio.serve_tcp(handler, 8181)


trio.run(run_server)
