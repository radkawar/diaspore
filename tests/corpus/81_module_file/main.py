import helper

print(__name__, __file__.replace("\\", "/").split("/")[-1])
print(helper.__name__, helper.__file__.replace("\\", "/").split("/")[-1])
print(helper.filename())
