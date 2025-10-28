<script>
   import { onMount, onDestroy } from "svelte";
   import mqtt from "mqtt";

   let mainSynth = null;
   let droneDevice = null;
   let audioContext = null;
   let magentaModel = null;
   let mqttClient = null;
   let currentPattern = "ripple";
   let isAnimating = false;
   let animationTimeout = null;

   const patterns = [
      { id: "ripple", name: "Ripple" },
      { id: "horizontal_lr", name: "Horizontal L→R" },
      { id: "horizontal_rl", name: "Horizontal R→L" },
      { id: "vertical_ud", name: "Vertical U→D" },
      { id: "vertical_du", name: "Vertical D→U" },
      { id: "random", name: "Random" },
   ];

   function setPattern(patternId) {
      currentPattern = patternId;
      if (mqttClient?.connected) {
         mqttClient.publish("pattern/set", patternId);
      }
      console.log(`Pattern set to: ${patternId}`);
   }

   function setupMQTT() {
      mqttClient = mqtt.connect("ws://localhost:9001");

      mqttClient.on("connect", () => {
         console.log("MQTT Connected");

         mqttClient.subscribe("switch/+");
         mqttClient.subscribe("pattern/set");
         mqttClient.subscribe("switch/regenerate");
      });

      mqttClient.on("message", (topic, message) => {
         const msg = message.toString();
         console.log(`${topic}: ${msg}`);

         const switchMatch = topic.match(/^switch\/(\d+)$/);
         if (switchMatch) {
            const switchNum = parseInt(switchMatch[1]);
            const state = msg.toLowerCase() === "on" || msg === "1";
            handlePhysicalSwitchUpdate(switchNum, state);
         }

         if (topic === "pattern/set") {
            currentPattern = msg.toLowerCase();
            console.log(`Pattern changed to: ${currentPattern}`);
         }

         if (topic === "switch/regenerate") {
            regenerateMelody();
         }
      });

      mqttClient.on("error", (err) => {
         console.error("MQTT Error:", err);
      });
   }

   function handlePhysicalSwitchUpdate(switchNum, state) {
      console.log(
         `Physical switch ${switchNum} is now ${state ? "ON" : "OFF"}`,
      );

      const row = Math.floor((switchNum - 1) / 4);
      const col = (switchNum - 1) % 4;

      if (window.p5Instance && !isAnimating) {
         window.p5Instance.triggerAnimation(row, col, state);
      }
   }

   function publishSwitchCommand(switchNum, state) {
      if (mqttClient?.connected) {
         const message = state ? "on" : "off";
         mqttClient.publish(`switch/${switchNum}`, message);
         console.log(`→ Microcontroller: switch/${switchNum} = ${message}`);
      }
   }

   function publishState(row, col, state, note) {
      if (mqttClient?.connected) {
         mqttClient.publish(
            "switch/state",
            JSON.stringify({
               row,
               col,
               state,
               note,
               timestamp: Date.now(),
            }),
         );
      }
   }

   async function setupRNBO() {
      const WAContext = window.AudioContext || window.webkitAudioContext;
      audioContext = new WAContext();

      const mainGain = audioContext.createGain();
      const droneGain = audioContext.createGain();
      const masterGain = audioContext.createGain();

      mainGain.gain.value = 0.7;
      droneGain.gain.value = 0.3;

      mainGain.connect(masterGain);
      droneGain.connect(masterGain);
      masterGain.connect(audioContext.destination);

      try {
         const synthResponse = await fetch("/export/synth.export.json");
         const droneResponse = await fetch("/export/drone.export.json");

         if (!synthResponse.ok)
            throw new Error(`Synth load failed: ${synthResponse.status}`);
         if (!droneResponse.ok)
            throw new Error(`Drone load failed: ${droneResponse.status}`);

         const synthPatcher = await synthResponse.json();
         const dronePatcher = await droneResponse.json();

         if (!window.RNBO) {
            await loadRNBOScript(synthPatcher.desc.meta.rnboversion);
         }

         mainSynth = await window.RNBO.createDevice({
            context: audioContext,
            patcher: synthPatcher,
         });

         droneDevice = await window.RNBO.createDevice({
            context: audioContext,
            patcher: dronePatcher,
         });

         mainSynth.node.connect(mainGain);
         droneDevice.node.connect(droneGain);

         console.log("RNBO devices loaded");

         setupMainSynth();
         setupDrone();
      } catch (err) {
         console.error("RNBO setup failed:", err);
      }
   }

   function setupDrone() {
      if (!droneDevice) return;

      try {
         const droneOnParam = droneDevice.parametersById.get("droneOn");
         if (droneOnParam) {
            droneOnParam.value = 1;
            console.log("droneOn = 1");
         }

         const midiPort = 0;
         const note = 48;
         const velocity = 100;
         const currentTime = droneDevice.context.currentTime * 1000;

         const noteOn = [144, note, velocity];
         droneDevice.scheduleEvent(
            new window.RNBO.MIDIEvent(currentTime, midiPort, noteOn),
         );

         console.log("Drone started");
      } catch (err) {
         console.error("Drone setup error:", err);
      }
   }

   function loadRNBOScript(version) {
      return new Promise((resolve, reject) => {
         const el = document.createElement("script");
         el.src = `https://c74-public.nyc3.digitaloceanspaces.com/rnbo/${encodeURIComponent(version)}/rnbo.min.js`;
         el.onload = resolve;
         el.onerror = () => reject(new Error("Failed to load rnbo.js"));
         document.body.append(el);
      });
   }

   function setupMainSynth() {
      if (!mainSynth) return;

      const params = {
         filterCut: 1000,
         filterQ: 0.1,
         reverbTime: 3,
         reverbMix: 0.5,
         filterType: 2,
         "poly/envelope/attack": 200,
         "poly/envelope/decay": 200,
         "poly/envelope/sustain": 0,
         "poly/envelope/release": 1000,
         "poly/oscillator/mode": 1,
         "poly/delay/fb": 0.75,
      };

      Object.entries(params).forEach(([key, value]) => {
         const param = mainSynth.parametersById.get(key);
         if (param) {
            param.value = value;
            console.log(`Main synth: ${key} = ${value}`);
         }
      });
   }

   function playMIDINote(note, duration = 250) {
      if (!mainSynth || !audioContext) return;

      if (audioContext.state === "suspended") {
         audioContext.resume();
      }

      const midiChannel = 0;
      const midiPort = 0;
      const velocity = 100;
      const currentTime = mainSynth.context.currentTime * 1000;

      const noteOnMessage = [144 + midiChannel, note, velocity];
      const noteOnEvent = new window.RNBO.MIDIEvent(
         currentTime,
         midiPort,
         noteOnMessage,
      );
      mainSynth.scheduleEvent(noteOnEvent);

      const noteOffMessage = [128 + midiChannel, note, 0];
      const noteOffEvent = new window.RNBO.MIDIEvent(
         currentTime + duration,
         midiPort,
         noteOffMessage,
      );
      mainSynth.scheduleEvent(noteOffEvent);
   }

   async function initializeMagenta() {
      try {
         magentaModel = new mm.MusicVAE(
            "https://storage.googleapis.com/magentadata/js/checkpoints/music_vae/trio_4bar",
         );
         await magentaModel.initialize();
         console.log("Magenta model loaded");
      } catch (err) {
         console.error("Magenta failed:", err);
      }
   }

   async function generateMelody(numNotes = 16) {
      if (!magentaModel) {
         console.warn("Model not ready, using random notes");
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }

      try {
         const samples = await magentaModel.sample(1, 0.7);
         let notes = samples[0].notes.slice(0, numNotes).map((n) => n.pitch);

         const pentatonic = [
            48, 50, 52, 55, 57, 60, 62, 64, 67, 69, 72, 74, 76,
         ];

         notes = notes.map((note) => {
            return pentatonic.reduce((closest, pNote) => {
               return Math.abs(pNote - note) < Math.abs(closest - note)
                  ? pNote
                  : closest;
            }, pentatonic[0]);
         });

         return notes;
      } catch (err) {
         console.error("Generation failed:", err);
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }
   }

   async function regenerateMelody() {
      const newNotes = await generateMelody(16);
      if (window.p5Instance) {
         window.p5Instance.updateNotes(newNotes);
      }
   }

   function loadP5() {
      const script = document.createElement("script");
      script.src = "/lib/p5.min.js";

      script.onload = () => {
         new window.p5((p) => {
            const canvasSize = 720;
            const rows = 4;
            const cols = 4;
            const ellipseSize = 100;
            let ellipseStates = [];
            let midiNotes = [];

            for (let i = 0; i < rows; i++) {
               ellipseStates[i] = [];
               midiNotes[i] = [];
               for (let j = 0; j < cols; j++) {
                  ellipseStates[i][j] = false;
                  midiNotes[i][j] = 60;
               }
            }

            const xSpacing = canvasSize / (cols + 1);
            const ySpacing = canvasSize / (rows + 1);

            function checkAllOff() {
               for (let i = 0; i < rows; i++) {
                  for (let j = 0; j < cols; j++) {
                     if (ellipseStates[i][j]) return false;
                  }
               }
               return true;
            }

            async function regenerateNotes() {
               if (checkAllOff()) {
                  console.log(
                     "All switches OFF - regenerating notes and shuffling pattern...",
                  );

                  // Generate new notes
                  const newNotes = await generateMelody(16);
                  let idx = 0;
                  for (let i = 0; i < rows; i++) {
                     for (let j = 0; j < cols; j++) {
                        midiNotes[i][j] = newNotes[idx++] || 60;
                     }
                  }
                  console.log("🎹 Notes regenerated:", midiNotes);

                  // Shuffle pattern randomly
                  const patternIds = [
                     "ripple",
                     "horizontal_lr",
                     "horizontal_rl",
                     "vertical_ud",
                     "vertical_du",
                     "random",
                  ];
                  const randomPattern =
                     patternIds[Math.floor(Math.random() * patternIds.length)];
                  currentPattern = randomPattern;

                  // Publish pattern change to MQTT
                  if (mqttClient?.connected) {
                     mqttClient.publish("pattern/set", randomPattern);
                  }

                  console.log(`Pattern shuffled to: ${randomPattern}`);
               }
            }

            function ripplePattern(clickedRow, clickedCol, newState) {
               const clickedX = xSpacing * (clickedCol + 1);
               const clickedY = ySpacing * (clickedRow + 1);
               let ellipsesWithDistance = [];

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     const distance = p.dist(clickedX, clickedY, x, y);
                     ellipsesWithDistance.push({
                        row: j,
                        col: i,
                        distance: distance,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               ellipsesWithDistance.sort((a, b) => a.distance - b.distance);

               const totalDuration = ellipsesWithDistance.length * 300;

               ellipsesWithDistance.forEach((ellipse, index) => {
                  setTimeout(() => {
                     updateSwitch(
                        ellipse.row,
                        ellipse.col,
                        newState,
                        ellipse.midiNote,
                     );

                     if (index === ellipsesWithDistance.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function horizontalDominoLR(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Create sequence in row-major order (left to right, top to bottom)
               for (let j = 0; j < rows; j++) {
                  for (let i = 0; i < cols; i++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               // Find clicked position in sequence
               const clickedIndex = clickedRow * cols + clickedCol;

               // Rotate sequence to start from clicked position
               const rotatedSequence = [
                  ...sequence.slice(clickedIndex),
                  ...sequence.slice(0, clickedIndex),
               ];

               const totalDuration = rotatedSequence.length * 150;

               rotatedSequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);

                     if (index === rotatedSequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function horizontalDominoRL(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Create sequence in row-major order (right to left, bottom to top)
               for (let j = rows - 1; j >= 0; j--) {
                  for (let i = cols - 1; i >= 0; i--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               // Find clicked position in reversed sequence
               const totalSwitches = rows * cols;
               const clickedIndex =
                  totalSwitches - 1 - (clickedRow * cols + clickedCol);

               // Rotate sequence to start from clicked position
               const rotatedSequence = [
                  ...sequence.slice(clickedIndex),
                  ...sequence.slice(0, clickedIndex),
               ];

               const totalDuration = rotatedSequence.length * 150;

               rotatedSequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);

                     if (index === rotatedSequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function verticalDominoUD(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Create sequence in column-major order (top to bottom, left to right)
               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               // Find clicked position in column-major sequence
               const clickedIndex = clickedCol * rows + clickedRow;

               // Rotate sequence to start from clicked position
               const rotatedSequence = [
                  ...sequence.slice(clickedIndex),
                  ...sequence.slice(0, clickedIndex),
               ];

               const totalDuration = rotatedSequence.length * 150;

               rotatedSequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);

                     if (index === rotatedSequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function verticalDominoDU(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Create sequence in column-major order (bottom to top, right to left)
               for (let i = cols - 1; i >= 0; i--) {
                  for (let j = rows - 1; j >= 0; j--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               // Find clicked position in reversed column-major sequence
               const totalSwitches = rows * cols;
               const clickedIndex =
                  totalSwitches - 1 - (clickedCol * rows + clickedRow);

               // Rotate sequence to start from clicked position
               const rotatedSequence = [
                  ...sequence.slice(clickedIndex),
                  ...sequence.slice(0, clickedIndex),
               ];

               const totalDuration = rotatedSequence.length * 150;

               rotatedSequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);

                     if (index === rotatedSequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function randomPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               for (let i = sequence.length - 1; i > 0; i--) {
                  const j = Math.floor(Math.random() * (i + 1));
                  [sequence[i], sequence[j]] = [sequence[j], sequence[i]];
               }

               const totalDuration = sequence.length * 100;

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);

                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * 300);
               });

               return totalDuration + 500;
            }

            function updateSwitch(row, col, state, midiNote) {
               ellipseStates[row][col] = state;

               const switchNum = row * cols + col + 1;
               publishSwitchCommand(switchNum, state);

               if (state) {
                  playMIDINote(midiNote, 200);
               }
            }

            function triggerAnimation(row, col, newState) {
               if (isAnimating) {
                  console.log("Animation in progress - ignoring input");
                  return;
               }

               isAnimating = true;
               publishState(row, col, newState, midiNotes[row][col]);

               let duration = 5000;

               switch (currentPattern) {
                  case "ripple":
                     duration = ripplePattern(row, col, newState);
                     break;
                  case "horizontal_lr":
                  case "domino_lr":
                     duration = horizontalDominoLR(row, col, newState);
                     break;
                  case "horizontal_rl":
                  case "domino_rl":
                     duration = horizontalDominoRL(row, col, newState);
                     break;
                  case "vertical_ud":
                  case "domino_ud":
                     duration = verticalDominoUD(row, col, newState);
                     break;
                  case "vertical_du":
                  case "domino_du":
                     duration = verticalDominoDU(row, col, newState);
                     break;
                  case "random":
                     duration = randomPattern(row, col, newState);
                     break;
                  default:
                     duration = ripplePattern(row, col, newState);
               }

               if (animationTimeout) clearTimeout(animationTimeout);
               animationTimeout = setTimeout(
                  () => {
                     isAnimating = false;
                     console.log("Animation lock released (failsafe)");
                  },
                  Math.max(duration, 5000),
               );
            }

            window.p5Instance = {
               triggerAnimation: (row, col, state) => {
                  if (row >= 0 && row < rows && col >= 0 && col < cols) {
                     triggerAnimation(row, col, state);
                  }
               },
               updateNotes: (newNotes) => {
                  let idx = 0;
                  for (let i = 0; i < rows; i++) {
                     for (let j = 0; j < cols; j++) {
                        midiNotes[i][j] = newNotes[idx++] || 60;
                     }
                  }
                  console.log("Notes updated via MQTT");
               },
               updateSwitchState: (row, col, state) => {
                  if (row >= 0 && row < rows && col >= 0 && col < cols) {
                     ellipseStates[row][col] = state;
                     if (state) {
                        playMIDINote(midiNotes[row][col], 200);
                     }
                  }
               },
            };

            generateMelody(rows * cols).then((generatedNotes) => {
               console.log("Generated notes:", generatedNotes);
               let noteIndex = 0;
               for (let i = 0; i < rows; i++) {
                  for (let j = 0; j < cols; j++) {
                     midiNotes[i][j] = generatedNotes[noteIndex] || 60;
                     noteIndex++;
                  }
               }
               console.log("🎹 Final grid:", midiNotes);
            });

            p.setup = () => {
               p.createCanvas(canvasSize, canvasSize);
               p.background(255);
               p.textAlign(p.CENTER, p.CENTER);
            };

            p.draw = () => {
               p.background(255);

               // p.fill(0);
               // p.textSize(20);
               // p.textAlign(p.LEFT, p.TOP);
               // p.text(`Pattern: ${currentPattern}`, 20, 20);

               p.textAlign(p.CENTER, p.CENTER);

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     p.fill(ellipseStates[j][i] ? 0 : 255);
                     p.stroke(0);
                     p.ellipse(x, y, ellipseSize, ellipseSize);
                     p.fill(ellipseStates[j][i] ? 255 : 0);
                     p.textSize(16);
                     p.text(ellipseStates[j][i] ? "ON" : "OFF", x, y - 8);
                     p.textSize(12);
                     p.text(`♪${midiNotes[j][i]}`, x, y + 10);
                  }
               }
            };

            p.mousePressed = () => {
               if (audioContext?.state === "suspended") {
                  audioContext.resume().then(() => {
                     console.log("Audio started");
                  });
               }

               if (isAnimating) {
                  console.log("Animation locked - cannot click");
                  return;
               }

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     const distance = p.dist(p.mouseX, p.mouseY, x, y);
                     if (distance < ellipseSize / 2) {
                        triggerAnimation(j, i, !ellipseStates[j][i]);
                        return;
                     }
                  }
               }
            };

            p.keyPressed = () => {
               switch (p.key) {
                  case "1":
                     currentPattern = "ripple";
                     console.log("Pattern: Ripple");
                     break;
                  case "2":
                     currentPattern = "horizontal_lr";
                     console.log("Pattern: Horizontal L→R");
                     break;
                  case "3":
                     currentPattern = "horizontal_rl";
                     console.log("Pattern: Horizontal R→L");
                     break;
                  case "4":
                     currentPattern = "vertical_ud";
                     console.log("Pattern: Vertical U→D");
                     break;
                  case "5":
                     currentPattern = "vertical_du";
                     console.log("Pattern: Vertical D→U");
                     break;
                  case "6":
                     currentPattern = "random";
                     console.log("Pattern: Random");
                     break;
               }
            };
         });
      };

      document.body.appendChild(script);
   }

   onMount(() => {
      setupRNBO();
      setupMQTT();

      const magentaScript = document.createElement("script");
      magentaScript.src = "/lib/magenta.js";
      magentaScript.onload = async () => {
         console.log("Magenta.js loaded");
         await initializeMagenta();
         console.log("Magenta model ready");
         loadP5();
      };
      document.body.appendChild(magentaScript);
   });

   onDestroy(() => {
      if (mqttClient) {
         mqttClient.end();
         console.log("MQTT disconnected");
      }
      if (animationTimeout) {
         clearTimeout(animationTimeout);
      }
   });
</script>

<!-- Pattern Selection UI (for debugging) -->
<!-- <div class="pattern-selector">
   <div class="pattern-buttons">
      {#each patterns as pattern}
         <button
            class:active={currentPattern === pattern.id}
            on:click={() => setPattern(pattern.id)}
            disabled={isAnimating}
         >
            {pattern.name}
         </button>
      {/each}
   </div>
</div> -->

<style>
   :global(html),
   :global(body) {
      margin: 0;
      padding: 0;
      overflow: hidden;
      width: 100vw;
      height: 100vh;
   }

   :global(body) {
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      background: #f0f0f0;
   }

   :global(*) {
      scrollbar-width: none;
      -ms-overflow-style: none;
   }

   :global(*::-webkit-scrollbar) {
      display: none;
   }

   .pattern-selector {
      position: fixed;
      top: 20px;
      right: 20px;
      background: white;
      padding: 20px;
      border-radius: 10px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      z-index: 1000;
   }

   .pattern-selector h3 {
      margin: 0 0 15px 0;
      font-size: 18px;
      font-weight: bold;
   }

   .pattern-buttons {
      display: flex;
      flex-direction: column;
      gap: 10px;
   }

   .pattern-buttons button {
      padding: 12px 20px;
      border: 2px solid #333;
      background: white;
      border-radius: 5px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      transition: all 0.2s;
   }

   .pattern-buttons button:hover:not(:disabled) {
      background: #f0f0f0;
      transform: translateY(-2px);
   }

   .pattern-buttons button.active {
      background: #333;
      color: white;
   }

   .pattern-buttons button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
   }
</style>
