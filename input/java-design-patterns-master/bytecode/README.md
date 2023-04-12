---
title: Bytecode
category: Behavioral
language: en
tag:
 - Game programming
---

## Intent

Allows encoding behavior as instructions for a virtual machine.

## Explanation

Real world example

> A team is working on a new game where wizards battle against each other. The wizard behavior 
> needs to be carefully adjusted and iterated hundreds of times through playtesting. It's not 
> optimal to ask the programmer to make changes each time the game designer wants to vary the 
> behavior, so the wizard behavior is implemented as a data-driven virtual machine.

In plain words

> Bytecode pattern enables behavior driven by data instead of code.

[Gameprogrammingpatterns.com](https://gameprogrammingpatterns.com/bytecode.html) documentation 
states:

> An instruction set defines the low-level operations that can be performed. A series of 
> instructions is encoded as a sequence of bytes. A virtual machine executes these instructions one 
> at a time, using a stack for intermediate values. By combining instructions, complex high-level 
> behavior can be defined.

**Programmatic Example**

One of the most important game objects is the `Wizard` class.

```java
@AllArgsConstructor
@Setter
@Getter
@Slf4j
public class Wizard {

  private int health;
  private int agility;
  private int wisdom;
  private int numberOfPlayedSounds;
  private int numberOfSpawnedParticles;

  public void playSound() {
    LOGGER.info("Playing sound");
    numberOfPlayedSounds++;
  }

  public void spawnParticles() {
    LOGGER.info("Spawning particles");
    numberOfSpawnedParticles++;
  }
}
```

Next, we show the available instructions for our virtual machine. Each of the instructions has its 
own semantics on how it operates with the stack data. For example, the ADD instruction takes the top
two items from the stack, adds them together and pushes the result to the stack.

```java
@AllArgsConstructor
@Getter
public enum Instruction {

  LITERAL(1),         // e.g. "LITERAL 0", push 0 to stack
  SET_HEALTH(2),      // e.g. "SET_HEALTH", pop health and wizard number, call set health
  SET_WISDOM(3),      // e.g. "SET_WISDOM", pop wisdom and wizard number, call set wisdom
  SET_AGILITY(4),     // e.g. "SET_AGILITY", pop agility and wizard number, call set agility
  PLAY_SOUND(5),      // e.g. "PLAY_SOUND", pop value as wizard number, call play sound
  SPAWN_PARTICLES(6), // e.g. "SPAWN_PARTICLES", pop value as wizard number, call spawn particles
  GET_HEALTH(7),      // e.g. "GET_HEALTH", pop value as wizard number, push wizard's health
  GET_AGILITY(8),     // e.g. "GET_AGILITY", pop value as wizard number, push wizard's agility
  GET_WISDOM(9),      // e.g. "GET_WISDOM", pop value as wizard number, push wizard's wisdom
  ADD(10),            // e.g. "ADD", pop 2 values, push their sum
  DIVIDE(11);         // e.g. "DIVIDE", pop 2 values, push their division
  // ...
}
```

At the heart of our example is the `VirtualMachine` class. It takes instructions as input and
executes them to provide the game object behavior.

```java
@Getter
@Slf4j
public class VirtualMachine {

  private final Stack<Integer> stack = new Stack<>();

  private final Wizard[] wizards = new Wizard[2];

  public VirtualMachine() {
    wizards[0] = new Wizard(randomInt(3, 32), randomInt(3, 32), randomInt(3, 32),
        0, 0);
    wizards[1] = new Wizard(randomInt(3, 32), randomInt(3, 32), randomInt(3, 32),
        0, 0);
  }

  public VirtualMachine(Wizard wizard1, Wizard wizard2) {
    wizards[0] = wizard1;
    wizards[1] = wizard2;
  }

  public void execute(int[] bytecode) {
    for (var i = 0; i < bytecode.length; i++) {
      Instruction instruction = Instruction.getInstruction(bytecode[i]);
      switch (instruction) {
        case LITERAL:
          // Read the next byte from the bytecode.
          int value = bytecode[++i];
          // Push the next value to stack
          stack.push(value);
          break;
        case SET_AGILITY:
          var amount = stack.pop();
          var wizard = stack.pop();
          setAgility(wizard, amount);
          break;
        case SET_WISDOM:
          amount = stack.pop();
          wizard = stack.pop();
          setWisdom(wizard, amount);
          break;
        case SET_HEALTH:
          amount = stack.pop();
          wizard = stack.pop();
          setHealth(wizard, amount);
          break;
        case GET_HEALTH:
          wizard = stack.pop();
          stack.push(getHealth(wizard));
          break;
        case GET_AGILITY:
          wizard = stack.pop();
          stack.push(getAgility(wizard));
          break;
        case GET_WISDOM:
          wizard = stack.pop();
          stack.push(getWisdom(wizard));
          break;
        case ADD:
          var a = stack.pop();
          var b = stack.pop();
          stack.push(a + b);
          break;
        case DIVIDE:
          a = stack.pop();
          b = stack.pop();
          stack.push(b / a);
          break;
        case PLAY_SOUND:
          wizard = stack.pop();
          getWizards()[wizard].playSound();
          break;
        case SPAWN_PARTICLES:
          wizard = stack.pop();
          getWizards()[wizard].spawnParticles();
          break;
        default:
          throw new IllegalArgumentException("Invalid instruction value");
      }
      LOGGER.info("Executed " + instruction.name() + ", Stack contains " + getStack());
    }
  }

  public void setHealth(int wizard, int amount) {
    wizards[wizard].setHealth(amount);
  }
  // other setters ->
  // ...
}
```

Now we can show the full example utilizing the virtual machine.

```java
  public static void main(String[] args) {

    var vm = new VirtualMachine(
        new Wizard(45, 7, 11, 0, 0),
        new Wizard(36, 18, 8, 0, 0));

    vm.execute(InstructionConverterUtil.convertToByteCode("LITERAL 0"));
    vm.execute(InstructionConverterUtil.convertToByteCode("LITERAL 0"));
    vm.execute(InstructionConverterUtil.convertToByteCode("GET_HEALTH"));
    vm.execute(InstructionConverterUtil.convertToByteCode("LITERAL 0"));
    vm.execute(InstructionConverterUtil.convertToByteCode("GET_AGILITY"));
    vm.execute(InstructionConverterUtil.convertToByteCode("LITERAL 0"));
    vm.execute(InstructionConverterUtil.convertToByteCode("GET_WISDOM"));
    vm.execute(InstructionConverterUtil.convertToByteCode("ADD"));
    vm.execute(InstructionConverterUtil.convertToByteCode("LITERAL 2"));
    vm.execute(InstructionConverterUtil.convertToByteCode("DIVIDE"));
    vm.execute(InstructionConverterUtil.convertToByteCode("ADD"));
    vm.execute(InstructionConverterUtil.convertToByteCode("SET_HEALTH"));
  }
```

Here is the console output.

```
16:20:10.193 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed LITERAL, Stack contains [0]
16:20:10.196 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed LITERAL, Stack contains [0, 0]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed GET_HEALTH, Stack contains [0, 45]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed LITERAL, Stack contains [0, 45, 0]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed GET_AGILITY, Stack contains [0, 45, 7]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed LITERAL, Stack contains [0, 45, 7, 0]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed GET_WISDOM, Stack contains [0, 45, 7, 11]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed ADD, Stack contains [0, 45, 18]
16:20:10.197 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed LITERAL, Stack contains [0, 45, 18, 2]
16:20:10.198 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed DIVIDE, Stack contains [0, 45, 9]
16:20:10.198 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed ADD, Stack contains [0, 54]
16:20:10.198 [main] INFO com.iluwatar.bytecode.VirtualMachine - Executed SET_HEALTH, Stack contains []
```

## Class diagram

![alt text](./etc/bytecode.urm.png "Bytecode class diagram")

## Applicability

Use the Bytecode pattern when you have a lot of behavior you need to define and your
game’s implementation language isn’t a good fit because:

* It’s too low-level, making it tedious or error-prone to program in.
* Iterating on it takes too long due to slow compile times or other tooling issues.
* It has too much trust. If you want to ensure the behavior being defined can’t break the game, you need to sandbox it from the rest of the codebase.

## Related patterns

* [Interpreter](https://java-design-patterns.com/patterns/interpreter/)

## Credits

* [Game programming patterns](http://gameprogrammingpatterns.com/bytecode.html)
