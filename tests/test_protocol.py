import asyncio
import json

from conftest import load

protocol = load("protocol")

# FIPS-197 appendix C.1 AES-128 example
KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")
CIPHER = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")


def test_aes_matches_the_published_test_vector():
    assert protocol.aes_encrypt(KEY, PLAIN) == CIPHER
    assert protocol.aes_decrypt(KEY, CIPHER) == PLAIN


def test_short_payload_is_one_final_chunk_with_length_prefix():
    assert protocol.frame_raw(b"\x01\x02\x03") == [b"\xff\x00\x03\x01\x02\x03"]


def test_long_payload_is_split_into_numbered_19_byte_chunks():
    payload = bytes(range(40))
    chunks = protocol.frame_raw(payload)
    assert [c[0] for c in chunks] == [1, 2, 0xFF]
    assert [len(c) for c in chunks] == [20, 20, 5]
    assert chunks[0][1:3] == b"\x00\x28"  # length prefix, 40
    assert b"".join(c[1:] for c in chunks) == b"\x00\x28" + payload


def test_cleartext_json_frame_carries_the_text():
    chunks = protocol.frame_json('{"a":1}')
    assert chunks == [b'\xff\x00\x07{"a":1}']


def test_encrypted_json_frame_round_trips_with_the_key():
    chunks = protocol.frame_json_encrypted(KEY, '{"a":1}')
    body = b"".join(c[1:] for c in chunks)
    assert len(body) == 16
    assert protocol.unframe(protocol.aes_decrypt(KEY, body)) == b'{"a":1}'


def test_unframe_strips_the_length_prefix_and_padding():
    assert protocol.unframe(b"\x00\x02hi\x00\x00") == b"hi"
    assert protocol.unframe(b"\x00") == b""


def test_rpc_request_shape():
    req = json.loads(protocol.rpc("123", 22, 17, [1]))
    assert req["M"] == "Request"
    assert req["P"] == [{"GMI": "123", "HI": 22, "SI": 17}, [1]]
    assert json.loads(protocol.rpc("123", 2, 1))["P"] == [{"GMI": "123", "HI": 2, "SI": 1}]


def test_reassembler_orders_chunks_and_signals_on_the_last():
    async def run():
        r = protocol.Reassembler()
        r.feed(b"\x02cd")
        r.feed(b"\x01ab")
        assert not r.event.is_set()
        r.feed(b"\xffef")
        assert r.event.is_set()
        assert r.assembled() == b"abcdef"
        r.reset()
        assert r.assembled() == b"" and not r.event.is_set()

    asyncio.run(run())
