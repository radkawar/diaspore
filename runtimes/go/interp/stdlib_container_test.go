package interp

import (
	"bytes"
	"compress/flate"
	"crypto/sha256"
	"encoding/binary"
	"strings"
	"sync"
	"testing"
)

func storeTestBundle() map[string]any {
	return map[string]any{
		"diaspore": float64(1), "main": "sample",
		"modules": map[string]any{"sample": map[string]any{
			"name": "sample", "file": "<stdlib>/sample.py", "body": []any{},
		}},
	}
}

func storeTestData(t *testing.T, bundle map[string]any, codec byte) []byte {
	return storeTestDataDict(t, bundle, codec, nil)
}

func storeTestDataDict(t *testing.T, bundle map[string]any, codec byte, dictionary []byte) []byte {
	t.Helper()
	payload, err := EncodeDSPB(bundle, nil)
	if err != nil {
		t.Fatal(err)
	}
	decoded := len(payload)
	if codec == 1 {
		var buffer bytes.Buffer
		writer, err := flate.NewWriterDict(&buffer, flate.BestCompression, dictionary)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write(payload); err != nil {
			t.Fatal(err)
		}
		if err := writer.Close(); err != nil {
			t.Fatal(err)
		}
		payload = buffer.Bytes()
	}
	index := make([]byte, 32)
	index = binary.LittleEndian.AppendUint16(index, uint16(len(dictionary)))
	index = append(index, dictionary...)
	index = binary.LittleEndian.AppendUint32(index, 1)
	for _, text := range []string{"sample", "<stdlib>/sample.py"} {
		index = binary.LittleEndian.AppendUint16(index, uint16(len(text)))
		index = append(index, text...)
	}
	index = binary.LittleEndian.AppendUint32(index, 0)
	index = binary.LittleEndian.AppendUint32(index, uint32(len(payload)))
	index = binary.LittleEndian.AppendUint32(index, uint32(decoded))
	index = append(index, codec)
	digest := sha256.Sum256(payload)
	index = append(index, digest[:]...)
	data := binary.LittleEndian.AppendUint32([]byte("DSPS02"), uint32(len(index)))
	digest = sha256.Sum256(index)
	data = append(data, digest[:]...)
	data = append(data, index...)
	return append(data, payload...)
}

func storeTestRehashIndex(data []byte) {
	size := int(binary.LittleEndian.Uint32(data[6:10]))
	digest := sha256.Sum256(data[stdlibHeaderSize : stdlibHeaderSize+size])
	copy(data[10:stdlibHeaderSize], digest[:])
}

func TestStdlibContainerIntegrityAndLaziness(t *testing.T) {
	for _, codec := range []byte{0, 1} {
		data := storeTestData(t, storeTestBundle(), codec)
		store, err := indexStdlib(data)
		if err != nil {
			t.Fatal(err)
		}
		if store.id != "sha256:"+strings.Repeat("0", 64) || len(store.names) != 1 || store.modules["sample"].ir != nil {
			t.Fatal("index did not retain identity and lazy module inventory")
		}
		module := store.modules["sample"]
		var wait sync.WaitGroup
		for range 8 {
			wait.Go(func() {
				ir, err := module.load()
				if err != nil || ir["file"] != "<stdlib>/sample.py" {
					t.Errorf("concurrent module load: %v", err)
				}
			})
		}
		wait.Wait()
		data[len(data)-1] ^= 1
		if _, err := module.load(); err != nil {
			t.Fatalf("cached module was decoded again: %v", err)
		}
		corrupt, err := indexStdlib(data)
		if err != nil {
			t.Fatalf("startup scanned unused payload bytes: %v", err)
		}
		bad := corrupt.modules["sample"]
		if _, err := bad.load(); err == nil || !strings.Contains(err.Error(), "digest mismatch") {
			t.Fatalf("corrupt payload accepted: %v", err)
		}
		data[len(data)-1] ^= 1
		if _, err := bad.load(); err == nil {
			t.Fatal("failed module integrity check was not cached")
		}
	}
}

func TestStdlibContainerRejectsMalformedIndex(t *testing.T) {
	original := storeTestData(t, storeTestBundle(), 0)
	fields := stdlibHeaderSize + 38 + 2 + len("sample") + 2 + len("<stdlib>/sample.py")
	cases := map[string]func([]byte) []byte{
		"magic":        func(data []byte) []byte { data[0] = 'X'; return data },
		"legacy_magic": func(data []byte) []byte { copy(data[:6], "DSPS01"); return data },
		"dictionary_bound": func(data []byte) []byte {
			binary.LittleEndian.PutUint16(data[stdlibHeaderSize+32:], 32769)
			return data
		},
		"dictionary_span": func(data []byte) []byte {
			binary.LittleEndian.PutUint16(data[stdlibHeaderSize+32:], 32768)
			return data
		},
		"index_digest": func(data []byte) []byte { data[10] ^= 1; return data },
		"index_length": func(data []byte) []byte { binary.LittleEndian.PutUint32(data[6:10], ^uint32(0)); return data },
		"count":        func(data []byte) []byte { binary.LittleEndian.PutUint32(data[stdlibHeaderSize+34:], 100); return data },
		"name":         func(data []byte) []byte { data[stdlibHeaderSize+40] = '.'; return data },
		"utf8":         func(data []byte) []byte { data[stdlibHeaderSize+40] = 255; return data },
		"file":         func(data []byte) []byte { data[stdlibHeaderSize+48] = 0; return data },
		"offset":       func(data []byte) []byte { binary.LittleEndian.PutUint32(data[fields:], 1); return data },
		"stored":       func(data []byte) []byte { binary.LittleEndian.PutUint32(data[fields+4:], 0); return data },
		"decoded":      func(data []byte) []byte { binary.LittleEndian.PutUint32(data[fields+8:], 0); return data },
		"raw_length":   func(data []byte) []byte { binary.LittleEndian.PutUint32(data[fields+8:], 6); return data },
		"stored_bound": func(data []byte) []byte {
			binary.LittleEndian.PutUint32(data[fields+4:], stdlibMaxModuleSize+1)
			return data
		},
		"decoded_bound": func(data []byte) []byte {
			binary.LittleEndian.PutUint32(data[fields+8:], stdlibMaxModuleSize+1)
			return data
		},
		"codec":            func(data []byte) []byte { data[fields+12] = 2; return data },
		"trailing_payload": func(data []byte) []byte { return append(data, 0) },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			data := mutate(bytes.Clone(original))
			if name != "index_digest" && name != "index_length" {
				storeTestRehashIndex(data)
			}
			if _, err := indexStdlib(data); err == nil {
				t.Fatal("invalid container index accepted")
			}
		})
	}
	for cut := range len(original) {
		if _, err := indexStdlib(original[:cut]); err == nil {
			t.Fatalf("truncated container accepted at %d", cut)
		}
	}
}

func TestStdlibContainerValidatesDecodedModule(t *testing.T) {
	cases := map[string]func(map[string]any){
		"main": func(bundle map[string]any) { bundle["main"] = "other" },
		"file": func(bundle map[string]any) { bundle["modules"].(map[string]any)["sample"].(node)["file"] = "other.py" },
		"extra_module": func(bundle map[string]any) {
			bundle["modules"].(map[string]any)["other"] = map[string]any{"body": []any{}, "file": "other.py"}
		},
		"linked": func(bundle map[string]any) { bundle["stdlib"] = "sha256:" + strings.Repeat("0", 64) },
		"expression_statement": func(bundle map[string]any) {
			bundle["modules"].(map[string]any)["sample"].(node)["body"] = []any{node{"op": "name", "id": "bad"}}
		},
		"invalid_constant": func(bundle map[string]any) {
			bundle["modules"].(map[string]any)["sample"].(node)["body"] = []any{node{"op": "expr", "v": node{"op": "const", "t": "int", "v": "bad"}}}
		},
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			bundle := storeTestBundle()
			mutate(bundle)
			store, err := indexStdlib(storeTestData(t, bundle, 1))
			if err != nil {
				t.Fatalf("index decoded module eagerly: %v", err)
			}
			if _, err := store.modules["sample"].load(); err == nil {
				t.Fatal("invalid decoded module accepted")
			}
		})
	}
}

func TestStdlibContainerChecksDecodedLength(t *testing.T) {
	for _, delta := range []int{-1, 1} {
		data := storeTestData(t, storeTestBundle(), 1)
		field := stdlibHeaderSize + 38 + 2 + len("sample") + 2 + len("<stdlib>/sample.py") + 8
		length := binary.LittleEndian.Uint32(data[field:])
		binary.LittleEndian.PutUint32(data[field:], uint32(int(length)+delta))
		storeTestRehashIndex(data)
		store, err := indexStdlib(data)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := store.modules["sample"].load(); err == nil {
			t.Fatal("decoded length mismatch accepted")
		}
	}
}

func TestStdlibContainerRejectsUnsortedOrDuplicateNames(t *testing.T) {
	for _, first := range []string{"sample", "zample"} {
		data := storeTestData(t, storeTestBundle(), 0)
		indexSize := int(binary.LittleEndian.Uint32(data[6:10]))
		index := bytes.Clone(data[stdlibHeaderSize : stdlibHeaderSize+indexSize])
		payload := data[stdlibHeaderSize+indexSize:]
		record := bytes.Clone(index[38:])
		secondOffset := 2 + len("sample") + 2 + len("<stdlib>/sample.py")
		binary.LittleEndian.PutUint32(record[secondOffset:], uint32(len(payload)))
		copy(index[40:], first)
		index = append(index, record...)
		binary.LittleEndian.PutUint32(index[34:], 2)
		container := binary.LittleEndian.AppendUint32([]byte("DSPS02"), uint32(len(index)))
		digest := sha256.Sum256(index)
		container = append(container, digest[:]...)
		container = append(container, index...)
		container = append(container, payload...)
		container = append(container, payload...)
		if _, err := indexStdlib(container); err == nil {
			t.Fatalf("module order %q, sample accepted", first)
		}
	}
}

func TestStdlibContainerChecksDeflateFraming(t *testing.T) {
	for name, mutate := range map[string]func([]byte) []byte{
		"truncated":     func(payload []byte) []byte { return payload[:len(payload)-1] },
		"trailing":      func(payload []byte) []byte { return append(payload, 0) },
		"concatenated":  func(payload []byte) []byte { return append(payload, payload...) },
		"invalid_block": func(payload []byte) []byte { payload[0] = 7; return payload },
		"one_byte":      func(payload []byte) []byte { return []byte{7} },
		"gzip_wrapper":  func(payload []byte) []byte { return append([]byte{0x1f, 0x8b, 8, 0}, payload...) },
	} {
		t.Run(name, func(t *testing.T) {
			data := storeTestData(t, storeTestBundle(), 1)
			indexSize := int(binary.LittleEndian.Uint32(data[6:10]))
			start := stdlibHeaderSize + indexSize
			payload := mutate(bytes.Clone(data[start:]))
			data = append(data[:start], payload...)
			fields := stdlibHeaderSize + 38 + 2 + len("sample") + 2 + len("<stdlib>/sample.py")
			binary.LittleEndian.PutUint32(data[fields+4:], uint32(len(payload)))
			digest := sha256.Sum256(payload)
			copy(data[fields+13:], digest[:])
			storeTestRehashIndex(data)
			store, err := indexStdlib(data)
			if err != nil {
				t.Fatalf("index inspected compressed payload: %v", err)
			}
			if _, err := store.modules["sample"].load(); err == nil {
				t.Fatal("malformed deflate payload accepted")
			}
		})
	}
}

func TestStdlibContainerPresetDictionary(t *testing.T) {
	bundle := storeTestBundle()
	dictionary, err := EncodeDSPB(bundle, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, codec := range []byte{0, 1} {
		for _, dict := range [][]byte{nil, dictionary, append(make([]byte, 32768-len(dictionary)), dictionary...)} {
			data := storeTestDataDict(t, bundle, codec, dict)
			store, err := indexStdlib(data)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := store.modules["sample"].load(); err != nil {
				t.Fatalf("codec %d, dictionary length %d: %v", codec, len(dict), err)
			}
		}
	}
	missing, err := indexStdlib(storeTestDataDict(t, bundle, 1, dictionary))
	if err != nil {
		t.Fatal(err)
	}
	missing.modules["sample"].dictionary = nil
	if _, err := missing.modules["sample"].load(); err == nil {
		t.Fatal("dictionary-dependent stream accepted without its dictionary")
	}
	data := storeTestDataDict(t, bundle, 1, dictionary)
	data[stdlibHeaderSize+34] ^= 1
	if _, err := indexStdlib(data); err == nil {
		t.Fatal("dictionary corruption bypassed index digest")
	}
	// Raw DEFLATE has no dictionary checksum. Rehash a wrong dictionary that
	// destroys the referenced DSPB bytes, then require lazy decode to reject it.
	for i := range dictionary {
		data[stdlibHeaderSize+34+i] = 0
	}
	storeTestRehashIndex(data)
	store, err := indexStdlib(data)
	if err != nil {
		t.Fatalf("index eagerly decoded dictionary-dependent payload: %v", err)
	}
	if _, err := store.modules["sample"].load(); err == nil {
		t.Fatal("wrong preset dictionary accepted")
	}
}
