class Animal:
    kind = "animal"

    def __init__(self, name):
        self.name = name

    def speak(self):
        return f"{self.name} makes a sound"

    def intro(self):
        return f"I am {self.name}, a {self.kind}"


class Dog(Animal):
    kind = "dog"

    def speak(self):
        return f"{self.name} barks"


class Puppy(Dog):
    def speak(self):
        base = super().speak()
        return base + " (softly)"


a = Animal("generic")
d = Dog("rex")
p = Puppy("bit")
print(a.speak(), a.intro())
print(d.speak(), d.intro())
print(p.speak(), p.intro())
print(isinstance(p, Puppy), isinstance(p, Dog), isinstance(p, Animal), isinstance(d, Puppy))
print(issubclass(Puppy, Animal), issubclass(Animal, Dog), issubclass(Dog, Dog))


class Base:
    def __init__(self, tag):
        self.tag = tag
        self.log = ["base"]


class Child(Base):
    def __init__(self, tag, extra):
        super().__init__(tag)
        self.log.append("child")
        self.extra = extra


c = Child("t1", 42)
print(c.tag, c.extra, c.log)


class A:
    def who(self):
        return "A"

    def greet(self):
        return "hi from " + self.who()


class B(A):
    def who(self):
        return "B"


class C(A):
    def who(self):
        return "C"

    def greet(self):
        return super().greet() + "!"


class D(B, C):
    pass


print(A().greet(), B().greet(), C().greet(), D().greet())
print(D().who())


class Shape:
    def area(self):
        raise NotImplementedError("subclass must implement")

    def describe(self):
        return f"{self.label()} area={self.area()}"

    def label(self):
        return "shape"


class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side ** 2

    def label(self):
        return "square"


class Circle(Shape):
    def __init__(self, r):
        self.r = r

    def area(self):
        return round(3.14159 * self.r ** 2, 2)

    def label(self):
        return "circle"


for s in [Square(3), Circle(1)]:
    print(s.describe())
try:
    Shape().area()
except NotImplementedError as e:
    print("caught:", e)


class Registry:
    entries = []

    @classmethod
    def add(cls, item):
        cls.entries.append(item)
        return cls

    @staticmethod
    def motto():
        return "keep it simple"


Registry.add("one").add("two")
print(Registry.entries, Registry.motto())
r = Registry()
print(r.motto(), r.entries)
