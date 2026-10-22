def average_age(users):
    total = 0
    for u in users:
        total += u["age"]
    return total / len(users)
