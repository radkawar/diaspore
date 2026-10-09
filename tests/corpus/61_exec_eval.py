print(eval("1 + 2 * 3"))
print(eval("[x*x for x in range(5)]"))
x = 10
print(eval("x + 5"))
exec("y = 100")
print(y)
ns = {}
exec("a = 1\nb = a + 2\ndef f():\n    return a + b", ns)
print(ns["a"], ns["b"], ns["f"]())
exec("for i in range(3): print('exec loop', i)")
c = compile("2 ** 10", "<test>", "eval")
print(eval(c))
try:
    eval("1 +")
except SyntaxError as e:
    print("syntax error caught")
g = {"n": 5}
print(eval("n * n", g))
