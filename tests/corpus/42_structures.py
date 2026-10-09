"""Real data structures in the diaspore subset: binary heap, LRU cache, trie,
union-find with path compression."""


class MinHeap:
    def __init__(self):
        self.a = []

    def push(self, v):
        self.a.append(v)
        i = len(self.a) - 1
        while i > 0:
            parent = (i - 1) // 2
            if self.a[parent] <= self.a[i]:
                break
            self.a[parent], self.a[i] = self.a[i], self.a[parent]
            i = parent

    def pop(self):
        if not self.a:
            raise IndexError("pop from empty heap")
        top = self.a[0]
        last = self.a.pop()
        if self.a:
            self.a[0] = last
            i = 0
            n = len(self.a)
            while True:
                left, right = 2 * i + 1, 2 * i + 2
                smallest = i
                if left < n and self.a[left] < self.a[smallest]:
                    smallest = left
                if right < n and self.a[right] < self.a[smallest]:
                    smallest = right
                if smallest == i:
                    break
                self.a[i], self.a[smallest] = self.a[smallest], self.a[i]
                i = smallest
        return top

    def __len__(self):
        return len(self.a)


h = MinHeap()
for v in [5, 3, 8, 1, 9, 2, 7]:
    h.push(v)
print([h.pop() for _ in range(len(h))])

heap2 = MinHeap()
for pair in [(3, "c"), (1, "a"), (2, "b")]:
    heap2.push(pair)
print(heap2.pop(), heap2.pop(), heap2.pop())


class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        if key not in self.data:
            self.misses += 1
            return None
        self.hits += 1
        value = self.data.pop(key)
        self.data[key] = value  # re-insert: most recently used is last
        return value

    def put(self, key, value):
        if key in self.data:
            self.data.pop(key)
        elif len(self.data) >= self.capacity:
            oldest = next(iter(self.data))
            del self.data[oldest]
        self.data[key] = value

    def keys_by_age(self):
        return list(self.data.keys())


lru = LRUCache(3)
for k, v in [("a", 1), ("b", 2), ("c", 3)]:
    lru.put(k, v)
print(lru.keys_by_age(), lru.get("a"))
print(lru.keys_by_age())
lru.put("d", 4)  # evicts "b" (least recently used)
print(lru.keys_by_age(), lru.get("b"), lru.get("c"))
print(f"hits={lru.hits} misses={lru.misses}")


class Trie:
    def __init__(self):
        self.root = {}

    def insert(self, word):
        node = self.root
        for ch in word:
            if ch not in node:
                node[ch] = {}
            node = node[ch]
        node["$"] = True

    def contains(self, word):
        node = self._walk(word)
        return node is not None and "$" in node

    def count_prefix(self, prefix):
        node = self._walk(prefix)
        return 0 if node is None else self._count(node)

    def _walk(self, s):
        node = self.root
        for ch in s:
            if ch not in node:
                return None
            node = node[ch]
        return node

    def _count(self, node):
        total = 1 if "$" in node else 0
        for ch, child in node.items():
            if ch != "$":
                total += self._count(child)
        return total


t = Trie()
for w in ["car", "card", "care", "cat", "dog", "do"]:
    t.insert(w)
print(t.contains("car"), t.contains("ca"), t.contains("dog"), t.contains("dogs"))
print(t.count_prefix("car"), t.count_prefix("ca"), t.count_prefix("do"), t.count_prefix("x"))


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.groups = n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        self.groups -= 1
        return True


uf = UnionFind(10)
edges = [(0, 1), (2, 3), (1, 2), (4, 5), (6, 7), (5, 6), (0, 3), (8, 9)]
for a, b in edges:
    print(f"union({a},{b}) -> {uf.union(a, b)} groups={uf.groups}")
print(uf.find(0) == uf.find(3), uf.find(4) == uf.find(7), uf.find(0) == uf.find(9))

comps = {}
for i in range(10):
    comps.setdefault(uf.find(i), []).append(i)
print(sorted(sorted(members) for members in comps.values()))
