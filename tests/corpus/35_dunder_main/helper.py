reported = __name__


def whoami():
    return f"helper module __name__ is {__name__}"


def maybe_main():
    if __name__ == "__main__":
        print("helper thinks it is main?!")
    else:
        print("helper knows it is imported")
