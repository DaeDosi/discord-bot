"""마인크래프트 Source RCON 프로토콜 최소 구현.

외부 의존성(mcrcon 등) 없이 표준 RCON 패킷 포맷만 직접 구현한다 — 인증(SERVERDATA_AUTH)
후 명령 실행(SERVERDATA_EXECCOMMAND) 한 번 보내고 응답을 받는 단발성 연결만 지원하면 되므로
멀티 패킷 응답 조립 등은 다루지 않는다.
"""
import asyncio
import struct

_TYPE_AUTH        = 3
_TYPE_EXECCOMMAND = 2


class RCONError(Exception):
    pass


async def rcon_command(host: str, port: int, password: str, command: str, timeout: float = 5.0) -> str:
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        await _send_packet(writer, _TYPE_AUTH, password)
        auth_id, _ = await _read_packet(reader, timeout)
        if auth_id == -1:
            raise RCONError("RCON 인증 실패 (비밀번호를 확인하세요)")

        await _send_packet(writer, _TYPE_EXECCOMMAND, command)
        _, response = await _read_packet(reader, timeout)
        return response
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _send_packet(writer: asyncio.StreamWriter, pkt_type: int, payload: str) -> None:
    body = struct.pack("<ii", 0, pkt_type) + payload.encode("utf-8") + b"\x00\x00"
    writer.write(struct.pack("<i", len(body)) + body)
    await writer.drain()


async def _read_packet(reader: asyncio.StreamReader, timeout: float) -> tuple[int, str]:
    length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout)
    length = struct.unpack("<i", length_bytes)[0]
    data = await asyncio.wait_for(reader.readexactly(length), timeout)
    request_id, _pkt_type = struct.unpack("<ii", data[:8])
    payload = data[8:-2].decode("utf-8", errors="replace")
    return request_id, payload
