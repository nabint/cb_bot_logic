CC = gcc
CFLAGS = -O2 -Wall -Wextra -std=c11 -fPIC
LDFLAGS = -shared -lm

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S), Darwin)
    TARGET = mcts_engine.dylib
    GDN_TARGET = libmcts_gdnative.dylib
else
    TARGET = mcts_engine.so
    GDN_TARGET = libmcts_gdnative.so
endif

SRC = mcts_engine.c bot_logic_c.c
GDN_SRC = mcts_gdnative.c mcts_engine.c bot_logic_c.c
GDN_INCLUDES = -I godot-headers

.PHONY: all gdnative clean

all: $(TARGET) $(GDN_TARGET)

$(TARGET): $(SRC) mcts_engine.h bot_logic_c.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $(TARGET) $(SRC)

gdnative: $(GDN_TARGET)

$(GDN_TARGET): $(GDN_SRC) mcts_engine.h bot_logic_c.h mcts_gdnative.c
	$(CC) $(CFLAGS) $(GDN_INCLUDES) $(LDFLAGS) -o $(GDN_TARGET) $(GDN_SRC)

clean:
	rm -f $(TARGET) $(GDN_TARGET)
