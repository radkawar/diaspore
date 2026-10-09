import shapes
from shapes import Circle, area_of, PI
import textutil as tu
from textutil import slugify

print("main starts")
c = Circle(2)
s = shapes.Square(3)
print(c.area(), s.area(), area_of(c), area_of(s))
print(PI, shapes.PI, shapes.describe(c))
print(tu.shout("hello"), slugify("Hello, World! 2024"))
print(tu.WORDS, len(tu.WORDS))
print(shapes.counter, tu.counter_from_shapes())
import shapes
print("re-import is cached:", shapes.counter)
