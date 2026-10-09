import helper

print("main sees __name__ =", __name__)
print("helper reported", helper.reported)
print(helper.whoami())

if __name__ == "__main__":
    print("running as a script")
else:
    print("imported as a module")

helper.maybe_main()
