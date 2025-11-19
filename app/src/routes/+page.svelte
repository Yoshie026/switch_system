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

   let droneNotes = [48, 52, 55, 60, 64, 67]; // Pure C major chord
   let droneActive = false;

   const patterns = [
      { id: "ripple", name: "Ripple" },
      { id: "horizontal_lr", name: "Horizontal L→R" },
      { id: "horizontal_rl", name: "Horizontal R→L" },
      { id: "vertical_ud", name: "Vertical U→D" },
      { id: "vertical_du", name: "Vertical D→U" },
      { id: "random", name: "Random" },
   ];

   function setupMQTT() {
      try {
         mqttClient = mqtt.connect("ws://localhost:9001");

         mqttClient.on("connect", () => {
            console.log("MQTT Connected");
            mqttClient.subscribe("switch/+");
            mqttClient.subscribe("switch/master");
            mqttClient.subscribe("pattern/set");
            mqttClient.subscribe("switch/regenerate");
         });

         mqttClient.on("message", (topic, message) => {
            const msg = message.toString();
            const switchMatch = topic.match(/^switch\/(\d+)$/);
            if (switchMatch) {
               const switchNum = parseInt(switchMatch[1]);
               const state = msg.toLowerCase() === "on" || msg === "1";
               handlePhysicalSwitchUpdate(switchNum, state);
            }

            if (topic === "pattern/set") {
               currentPattern = msg.toLowerCase();
            }

            if (topic === "switch/regenerate") {
               regenerateMelody();
            }

            if (topic === "switch/master") {
               const isOn = msg.toLowerCase() === "on" || msg === "1";
               handleMasterSwitch(isOn);
            }
         });

         mqttClient.on("error", (err) => {
            console.error("MQTT Error:", err);
         });
      } catch (err) {
         console.error("MQTT setup failed:", err);
      }
   }

   function handlePhysicalSwitchUpdate(switchNum, state) {
      const row = Math.floor((switchNum - 1) / 4);
      const col = (switchNum - 1) % 4;
      const flippedCol = 3 - col;
      if (window.p5Instance && !isAnimating) {
         window.p5Instance.triggerAnimation(row, flippedCol, state);
      }
   }

   function handleMasterSwitch(isOn) {
      if (!window.p5Instance || isAnimating) return;
      isAnimating = true;

      const sequence = [];
      const rows = 4;
      const cols = 4;

      if (isOn) {
         for (let i = 0; i < cols; i++) {
            for (let j = 0; j < rows; j++) {
               sequence.push({ row: j, col: i });
            }
         }
      } else {
         for (let i = cols - 1; i >= 0; i--) {
            for (let j = rows - 1; j >= 0; j--) {
               sequence.push({ row: j, col: i });
            }
         }
      }

      sequence.forEach((item, index) => {
         setTimeout(() => {
            // Only update visual and play audio - NO physical switches
            window.p5Instance.updateSwitchVisualOnly(item.row, item.col, isOn);
            if (index === sequence.length - 1) {
               setTimeout(() => {
                  isAnimating = false;
               }, 500);
            }
         }, index * 150);
      });
   }

   // function publishSwitchCommand(row, col, state) {
   //    const flippedCol = 3 - col;
   //    const switchNum = row * 4 + flippedCol + 1;
   //    if (mqttClient?.connected) {
   //       mqttClient.publish(`switch/${switchNum}`, state ? "ON" : "OFF");
   //    }
   // }

   function publishSwitchCommand(row, col, state) {
      const flippedCol = 3 - col;
      const switchNum = row * 4 + flippedCol + 1;

      if (mqttClient?.connected) {
         mqttClient.publish(`switch/${switchNum}`, state ? "ON" : "OFF");
      }

      console.log(`[${row},${col}] → switch #${switchNum}`); // Debug
   }

   function publishState(row, col, state, note) {
      if (mqttClient?.connected) {
         mqttClient.publish(
            "switch/state",
            JSON.stringify({ row, col, state, note, timestamp: Date.now() }),
         );
      }
   }

   async function setupRNBO() {
      const WAContext = window.AudioContext || window.webkitAudioContext;
      audioContext = new WAContext();

      const mainGain = audioContext.createGain();
      const droneGain = audioContext.createGain();
      const masterGain = audioContext.createGain();

      mainGain.gain.value = 0.8;
      droneGain.gain.value = 0.1;
      masterGain.gain.value = 1.0;

      mainGain.connect(masterGain);
      droneGain.connect(masterGain);
      masterGain.connect(audioContext.destination);

      console.log("\nGAIN SETUP:");
      console.log("   Main: " + mainGain.gain.value);
      console.log("   Drone: " + droneGain.gain.value);
      console.log("   Master: " + masterGain.gain.value);

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

         console.log("\nRNBO devices created");

         console.log("\nMAIN SYNTH PARAMETERS:");
         mainSynth.parameters.forEach((param) => {
            console.log(
               `   - ${param.id}: ${param.value} [${param.min} to ${param.max}]`,
            );
         });

         console.log("\nDRONE PARAMETERS:");
         droneDevice.parameters.forEach((param) => {
            console.log(
               `   - ${param.id}: ${param.value} [${param.min} to ${param.max}]`,
            );
         });

         setupMainSynth();
         setupDrone();
      } catch (err) {
         console.error("RNBO setup failed:", err);
      }
   }

   let synthParams = {};

   function setupMainSynth() {
      if (!mainSynth) {
         console.error("mainSynth is null!");
         return;
      }

      console.log("\nSETTING UP MAIN SYNTH...");

      synthParams = {
         filterCut: mainSynth.parametersById.get("filterCut"),
         filterQ: mainSynth.parametersById.get("filterQ"),
         filterType: mainSynth.parametersById.get("filterType"),
         reverbTime: mainSynth.parametersById.get("reverbTime"),
         reverbMix: mainSynth.parametersById.get("reverbMix"),
         setTuning: mainSynth.parametersById.get("setTuning"),
         attack: mainSynth.parametersById.get("poly/envelope/attack"),
         decay: mainSynth.parametersById.get("poly/envelope/decay"),
         sustain: mainSynth.parametersById.get("poly/envelope/sustain"),
         release: mainSynth.parametersById.get("poly/envelope/release"),
         oscMode: mainSynth.parametersById.get("poly/oscillator/mode"),
         leftDelay: mainSynth.parametersById.get("poly/delay/left_delay"),
         delayFb: mainSynth.parametersById.get("poly/delay/fb"),
         rightDelay: mainSynth.parametersById.get("poly/delay/right_delay"),
      };

      const initialValues = {
         filterCut: 1200,
         filterQ: 0.2,
         filterType: 0,
         reverbTime: 10,
         reverbMix: 0.6,
         setTuning: 0,
         attack: 30,
         decay: 150,
         sustain: 0.6,
         release: 800,
         oscMode: 1,
         leftDelay: 250,
         delayFb: 0.4,
         rightDelay: 350,
      };

      Object.entries(initialValues).forEach(([key, value]) => {
         if (synthParams[key]) {
            synthParams[key].value = value;
            console.log(`   ${key} = ${value}`);
         } else {
            console.warn(`   NOT FOUND: ${key}`);
         }
      });

      console.log("Main synth setup complete");
   }

   function setupDrone() {
      if (!droneDevice) {
         console.error("droneDevice is null!");
         return;
      }

      console.log("\nSETTING UP DRONE...");

      const droneParams = {
         volume: 0.5, // Increased for better presence
         droneOn: 1,
         droneFilterType: 0, // Lowpass
         droneFilterCut: 800, // Warmer, darker (was 1200)
         droneFilterQ: 0.3, // Gentler resonance (was 0.7)
         harmonics: 1.5, // Simple, pure (was 5.0 - too complex/eerie)
         overblow: 0, // Minimal distortion (was 0.8 - too harsh)
         fluctuate: 0.005, // Very stable (was 0.01)
         reverbMix: 0.6, // Moderate reverb (was 0.8)
         reverb_decay: 8, // Longer, smoother tail
         reverb_rotate: 0.3, // Less swirling (was 0.8 - disorienting)
         damping: 0.75, // High damping removes harsh highs (was 0.4)
      };

      Object.entries(droneParams).forEach(([key, value]) => {
         const param = droneDevice.parametersById.get(key);
         if (param) {
            param.value = value;
            console.log(`   ${key} = ${value}`);
         } else {
            console.warn(`   NOT FOUND: ${key}`);
         }
      });

      const midiPort = 0;
      const velocity = 50;
      const currentTime = droneDevice.context.currentTime * 1000;

      console.log("\nStarting drone: C major chord (C3, E3, G3, C4, E4, G4)");
      console.log("Notes: " + droneNotes.join(", "));

      droneNotes.forEach((note) => {
         const noteOn = [144, note, velocity];
         droneDevice.scheduleEvent(
            new window.RNBO.MIDIEvent(currentTime, midiPort, noteOn),
         );
      });

      droneActive = true;
      console.log("Drone started");
   }

   function playMIDINote(note, duration = 500) {
      if (!mainSynth) {
         console.error("Cannot play - mainSynth is null");
         return;
      }

      if (!audioContext) {
         console.error("Cannot play - audioContext is null");
         return;
      }

      if (audioContext.state === "suspended") {
         audioContext.resume();
         console.log("Audio context resumed");
      }

      const midiChannel = 0;
      const midiPort = 0;
      const velocity = 127;
      const currentTime = audioContext.currentTime * 1000;

      console.log(
         `NOTE ${note}: vel=${velocity}, dur=${duration}ms, state=${audioContext.state}`,
      );

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

   function loadRNBOScript(version) {
      return new Promise((resolve, reject) => {
         const el = document.createElement("script");
         el.src = `/lib/rnbo.min.js`;
         el.onload = resolve;
         el.onerror = () => reject(new Error("Failed to load rnbo.js"));
         document.body.append(el);
      });
   }

   async function initializeMagenta() {
      try {
         if (!window.mm) {
            console.error("Magenta.js (mm) not loaded");
            return;
         }

         magentaModel = new window.mm.MusicVAE(
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
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }

      try {
         const samples = await magentaModel.sample(1, 0.7);
         let notes = samples[0].notes.slice(0, numNotes).map((n) => n.pitch);

         const scales = {
            pentatonic: [48, 50, 52, 55, 57, 60, 62, 64, 67, 69, 72, 74, 76],
            major: [
               48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71, 72, 74,
               76,
            ],
            minor: [
               48, 50, 51, 53, 55, 56, 58, 60, 62, 63, 65, 67, 68, 70, 72, 74,
               75,
            ],
            blues: [48, 51, 53, 54, 55, 58, 60, 63, 65, 66, 67, 70, 72, 75],
            dorian: [
               48, 50, 51, 53, 55, 57, 58, 60, 62, 63, 65, 67, 69, 70, 72, 74,
            ],
            phrygian: [
               48, 49, 51, 53, 55, 56, 58, 60, 61, 63, 65, 67, 68, 70, 72, 73,
            ],
            wholetone: [
               48, 50, 52, 54, 56, 58, 60, 62, 64, 66, 68, 70, 72, 74, 76,
            ],
            chromatic: [
               48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63,
               64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76,
            ],
         };

         const scaleNames = Object.keys(scales);
         const selectedScale =
            scaleNames[Math.floor(Math.random() * scaleNames.length)];
         const scale = scales[selectedScale];

         notes = notes.map((note) => {
            return scale.reduce((closest, scaleNote) => {
               return Math.abs(scaleNote - note) < Math.abs(closest - note)
                  ? scaleNote
                  : closest;
            }, scale[0]);
         });

         return notes;
      } catch (err) {
         console.error("Generation failed:", err);
         return Array(numNotes)
            .fill()
            .map(() => Math.floor(Math.random() * 37) + 48);
      }
   }

   function randomizeParams() {
      if (!mainSynth) return;

      const mode = Math.floor(Math.random() * 3) + 1;
      synthParams.oscMode.value = mode;
      switch (mode) {
         case 1:
            synthParams.reverbMix.value = 0.8;
            synthParams.reverb_decay.value = 0.7;
            synthParams.delayFb.value = 0.8;
            synthParams.reverbTime.value = 17;
            break;
         case 2:
            synthParams.filterCut = 1300;
            synthParams.reverbMix.value = 0.2;
            synthParams.filterCut.value = 500;
            break;
         case 3:
            synthParams.filterCut = 1300;
            synthParams.filterCut.value = 500;
            droneParams.harmonics.value = 3;
            break;
      }
      console.log(`Oscillator mode set to: ${mode}`);
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
            const xSpacing = canvasSize / (cols + 1);
            const ySpacing = canvasSize / (rows + 1);
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
                  const newNotes = await generateMelody(16);
                  let idx = 0;
                  for (let i = 0; i < rows; i++) {
                     for (let j = 0; j < cols; j++) {
                        midiNotes[i][j] = newNotes[idx++] || 60;
                     }
                  }
                  randomizeParams();

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

                  if (mqttClient?.connected) {
                     mqttClient.publish("pattern/set", randomPattern);
                  }
                  console.log("Notes regenerated");
                  addNoteToDrone();
               }
            }

            function addNoteToDrone() {
               if (!droneDevice) return;

               // const availableNotes = [
               //    36, 40, 43, 48, 52, 55, 60, 64, 67, 72, 76, 79,
               // ];

               const availableNotes = [
                  36, 40, 43, 48, 52, 55, 59, 60, 64, 67, 71, 74, 76, 79, 83,
               ];
               const unusedNotes = availableNotes.filter(
                  (note) => !droneNotes.includes(note),
               );

               if (unusedNotes.length === 0) {
                  console.log("DRONE: All notes already playing (max reached)");
                  return;
               }

               const newNote =
                  unusedNotes[Math.floor(Math.random() * unusedNotes.length)];
               droneNotes.push(newNote);

               const midiPort = 0;
               const velocity = 50;
               const currentTime = droneDevice.context.currentTime * 1000;

               const noteOn = [144, newNote, velocity];
               droneDevice.scheduleEvent(
                  new window.RNBO.MIDIEvent(currentTime, midiPort, noteOn),
               );

               console.log(
                  `DRONE: Added note ${newNote} (now playing ${droneNotes.length} notes: ${droneNotes.join(", ")})`,
               );
            }

            function resetDrone() {
               if (!droneDevice) return;

               const midiPort = 0;
               const currentTime = droneDevice.context.currentTime * 1000;

               droneNotes.forEach((note) => {
                  const noteOff = [128, note, 0];
                  droneDevice.scheduleEvent(
                     new window.RNBO.MIDIEvent(currentTime, midiPort, noteOff),
                  );
               });

               console.log(`DRONE: Stopped ${droneNotes.length} notes`);

               droneNotes = [48, 52, 55, 60, 64, 67]; // Pure C major chord

               const velocity = 50;
               droneNotes.forEach((note) => {
                  const noteOn = [144, note, velocity];
                  droneDevice.scheduleEvent(
                     new window.RNBO.MIDIEvent(
                        currentTime + 50,
                        midiPort,
                        noteOn,
                     ),
                  );
               });

               console.log(
                  `DRONE: Reset to original (${droneNotes.length} notes): ${droneNotes.join(", ")}`,
               );
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

               return ellipsesWithDistance.length * 300 + 500;
            }

            function updateSwitch(row, col, state, midiNote) {
               ellipseStates[row][col] = state;

               if (state) {
                  console.log(`Switch [${row},${col}] ON -> note ${midiNote}`);
                  playMIDINote(midiNote, 500);
               }

               publishSwitchCommand(row, col, state);
               publishState(row, col, state, midiNote);
            }

            function triggerAnimation(row, col, newState) {
               if (isAnimating) {
                  return;
               }

               isAnimating = true;
               const duration = ripplePattern(row, col, newState);

               if (animationTimeout) clearTimeout(animationTimeout);
               animationTimeout = setTimeout(
                  () => {
                     isAnimating = false;
                  },
                  Math.max(duration, 5000),
               );
            }

            // window.p5Instance = {
            //    triggerAnimation: (row, col, state) => {
            //       if (row >= 0 && row < rows && col >= 0 && col < cols) {
            //          triggerAnimation(row, col, state);
            //       }
            //    },
            //    updateNotes: (newNotes) => {
            //       let idx = 0;
            //       for (let i = 0; i < rows; i++) {
            //          for (let j = 0; j < cols; j++) {
            //             midiNotes[i][j] = newNotes[idx++] || 60;
            //          }
            //       }
            //    },
            //    getEllipseStates: () => ellipseStates,
            // };

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
               },
               getEllipseStates: () => ellipseStates,

               updateSwitchVisualOnly: (row, col, state) => {
                  if (row >= 0 && row < rows && col >= 0 && col < cols) {
                     ellipseStates[row][col] = state;
                     if (state) {
                        playMIDINote(midiNotes[row][col], 500);
                     }
                  }
               },
            };
            generateMelody(rows * cols).then((generatedNotes) => {
               let noteIndex = 0;
               for (let i = 0; i < rows; i++) {
                  for (let j = 0; j < cols; j++) {
                     midiNotes[i][j] = generatedNotes[noteIndex] || 60;
                     noteIndex++;
                  }
               }
            });

            p.setup = () => {
               p.createCanvas(canvasSize, canvasSize);
               p.background(255);
               p.textAlign(p.CENTER, p.CENTER);
            };

            p.draw = () => {
               p.background(255);
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
                     p.text(`${midiNotes[j][i]}`, x, y + 10);
                  }
               }
            };

            p.mousePressed = () => {
               if (audioContext?.state === "suspended") {
                  audioContext.resume().then(() => {
                     console.log("Audio context resumed");
                  });
               }

               if (isAnimating) return;

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
               if (p.key === " ") {
                  console.log("\nSPACEBAR TEST");
                  playMIDINote(60, 1000);
               }
               if (p.key === "d" || p.key === "D") {
                  if (droneDevice) {
                     const param = droneDevice.parametersById.get("droneOn");
                     if (param) {
                        param.value = param.value ? 0 : 1;
                        console.log(`DRONE: ${param.value ? "ON" : "OFF"}`);
                     }
                  }
               }
               if (p.key === "r" || p.key === "R") {
                  console.log("\nRESETTING DRONE...");
                  resetDrone();
               }
               if (p.key === "l" || p.key === "L") {
                  console.log(
                     `\nDRONE: Current notes (${droneNotes.length}): ${droneNotes.join(", ")}`,
                  );
               }
            };
         });
      };

      document.body.appendChild(script);
   }

   onMount(() => {
      console.log("\nSTARTING...\n");
      setupRNBO();
      setupMQTT();

      const magentaScript = document.createElement("script");
      magentaScript.src = "/lib/magenta.js";
      magentaScript.onload = async () => {
         await initializeMagenta();
         loadP5();
         console.log("\nREADY\n");
         console.log("CONTROLS:");
         console.log("   SPACEBAR = Test synth note");
         console.log("   D = Toggle drone on/off");
         console.log("   R = Reset drone to original notes");
         console.log("   L = List current drone notes");
         console.log("   Click = Trigger animation");
         console.log("\nDRONE EVOLUTION:");
         console.log("   - Starts with 6 notes (pure C major chord)");
         console.log("   - Adds 1 random major chord note each regeneration");
         console.log("   - Max 12 different notes");
         console.log("   - Press R to reset anytime\n");
      };
      document.body.appendChild(magentaScript);
   });

   onDestroy(() => {
      if (mqttClient) {
         mqttClient.end();
      }
      if (animationTimeout) {
         clearTimeout(animationTimeout);
      }
   });
</script>

<style>
   @font-face {
      font-family: "Cardinal";
      src: url("../css/fonts/Cardinal.ttf");
   }
   :global(html),
   :global(body) {
      font-family: Cardinal;
      margin: 0;
      padding: 0;
      overflow: hidden;
      width: 100vw;
      height: 100vh;
   }

   :global(body) {
      font-family: Cardinal;
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      background: #f0f0f0;
   }
</style>
