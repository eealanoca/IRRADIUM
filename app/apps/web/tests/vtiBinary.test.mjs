import assert from 'node:assert/strict';
import test from 'node:test';
import { deflateSync, inflateSync } from 'node:zlib';
import { normalizeRawVti } from '../components/results/vtiBinary.ts';

function file(payload, compressed = true, width = 4) {
  const data = compressed ? deflateSync(payload) : payload;
  const header = Buffer.alloc(width * (compressed ? 4 : 1));
  const numbers = compressed ? [1, 32768, payload.length, data.length] : [payload.length];
  numbers.forEach((value, index) => width === 8 ? header.writeBigUInt64LE(BigInt(value), index * width) : header.writeUInt32LE(value, index * width));
  const binary = Buffer.concat([header, data]);
  const prefix = `<VTKFile type="ImageData" byte_order="LittleEndian" header_type="UInt${width * 8}"${compressed ? ' compressor="vtkZLibDataCompressor"' : ''}><ImageData><CellData><DataArray type="UInt8" Name="example" format="appended" offset="0"/></CellData></ImageData><AppendedData encoding="raw">\n_`;
  return { buffer: Uint8Array.from(Buffer.concat([Buffer.from(prefix), binary, Buffer.from('\n  </AppendedData>\n</VTKFile>')])).buffer, binary, headerLength: header.length };
}

test('preserves a compressed checksum ending in newline instead of trimming it', () => {
  const payload = Buffer.from([1, 2, 6]);
  const input = file(payload);
  assert.equal(input.binary.at(-1), 10); // The exact I20 failure condition.
  const xml = new TextDecoder().decode(normalizeRawVti(input.buffer));
  assert.ok(!xml.includes('<AppendedData'));
  const encoded = /<DataArray[^>]*format="binary"[^>]*>([^<]+)<\/DataArray>/.exec(xml)[1];
  const binary = Buffer.from(encoded, 'base64');
  assert.deepEqual(binary, input.binary);
  assert.deepEqual(inflateSync(binary.subarray(input.headerLength)), payload);
});

test('preserves UInt64 compressed block headers and payloads', () => {
  const input = file(Buffer.from([1, 2, 6]), true, 8);
  const xml = new TextDecoder().decode(normalizeRawVti(input.buffer));
  const encoded = /<DataArray[^>]*>([^<]+)<\/DataArray>/.exec(xml)[1];
  assert.deepEqual(Buffer.from(encoded, 'base64'), input.binary);
});

test('preserves uncompressed trailing whitespace bytes as data', () => {
  const input = file(Buffer.from([10, 32, 9]), false);
  const xml = new TextDecoder().decode(normalizeRawVti(input.buffer));
  const encoded = /<DataArray[^>]*>([^<]+)<\/DataArray>/.exec(xml)[1];
  assert.deepEqual(Buffer.from(encoded, 'base64'), input.binary);
});

test('does not transform XML that already uses inline binary', () => {
  const input = new TextEncoder().encode('<VTKFile><DataArray format="binary">AA==</DataArray></VTKFile>').buffer;
  assert.equal(normalizeRawVti(input), input);
});
