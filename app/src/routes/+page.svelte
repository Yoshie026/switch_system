<script>
   import { onMount, onDestroy } from "svelte";
   import mqtt from "mqtt";

   // COMPLETELY SEPARATE: Main synth and Drone
   let mainSynth = null; // For playing notes only
   let droneDevice = null; // For background drone only
   let audioContext = null;
   let magentaModel = null;
   let mqttClient = null;
   let currentPattern = "ripple";
   let isAnimating = false;
   let animationTimeout = null;

   // DRONE-ONLY variables (nothing to do with synth)
   let droneNotes = [36, 48, 55, 60, 67]; // C & G harmony
   let droneActive = false;

   const patterns = [
      { id: "ripple", name: "Ripple" },
      { id: "horizontal_lr", name: "Horizontal L→R" },
      { id: "horizontal_rl", name: "Horizontal R→L" },
      { id: "vertical_ud", name: "Vertical U→D" },
      { id: "vertical_du", name: "Vertical D→U" },
      { id: "random", name: "Random" },
   ];

   const patternPresets = {
      ripple: {
         filterCut: 1200,
         filterQ: 0.1,
         reverbMix: 0.25,
         "poly/envelope/attack": 40,
         "poly/envelope/release": 600,
         "poly/oscillator/mode": 1,
      },

      horizontal_lr: {
         filterCut: 1500,
         filterQ: 0.18,
         reverbMix: 0.3,
         "poly/envelope/attack": 10,
         "poly/envelope/release": 300,
         "poly/oscillator/mode": 0,
      },

      horizontal_rl: {
         filterCut: 900,
         filterQ: 0.2,
         reverbMix: 0.35,
         "poly/envelope/attack": 80,
         "poly/envelope/release": 900,
         "poly/oscillator/mode": 2,
      },

      vertical_ud: {
         filterCut: 700,
         filterQ: 0.12,
         reverbMix: 0.2,
         "poly/envelope/attack": 60,
         "poly/envelope/release": 500,
         "poly/oscillator/mode": 3,
      },

      vertical_du: {
         filterCut: 500,
         filterQ: 0.15,
         reverbMix: 0.4,
         "poly/envelope/attack": 120,
         "poly/envelope/release": 1000,
         "poly/oscillator/mode": 4,
      },

      random: {
         filterCut: 600 + Math.random() * 1200,
         filterQ: 0.1 + Math.random() * 0.2,
         reverbMix: 0.2 + Math.random() * 0.4,
         "poly/envelope/attack": 20 + Math.random() * 120,
         "poly/envelope/release": 300 + Math.random() * 900,
         "poly/oscillator/mode": Math.floor(Math.random() * 5),
      },
   };

   function setPattern(patternId) {
      currentPattern = patternId;

      if (mqttClient?.connected) {
         mqttClient.publish("pattern/set", patternId);
      }

      if (patternPresets[patternId]) {
         const preset = patternPresets[patternId];
         applySynthParams(preset);
      }

      console.log(`Pattern set to: ${patternId}`);
   }

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
            console.log(`${topic}: ${msg}`);

            const switchMatch = topic.match(/^switch\/(\d+)$/);
            if (switchMatch) {
               const switchNum = parseInt(switchMatch[1]);
               const state = msg.toLowerCase() === "on" || msg === "1";
               handlePhysicalSwitchUpdate(switchNum, state);
            }

            if (topic === "pattern/set") {
               currentPattern = msg.toLowerCase();
               if (patternPresets[msg]) applySynthParams(patternPresets[msg]);
               console.log(`Pattern changed to: ${currentPattern}`);
            }

            if (topic === "switch/regenerate") {
               regenerateMelody();
            }

            if (topic === "switch/master") {
               const isOn = msg.toLowerCase() === "on" || msg === "1";
               console.log(`🔘 Master switch: ${isOn ? "ON" : "OFF"}`);
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
      console.log(
         `Physical switch ${switchNum} is now ${state ? "ON" : "OFF"}`,
      );
      const row = Math.floor((switchNum - 1) / 4);
      const col = (switchNum - 1) % 4;
      const flippedCol = 3 - col;
      if (window.p5Instance && !isAnimating) {
         window.p5Instance.triggerAnimation(row, flippedCol, state);
      }
   }

   function handleMasterSwitch(isOn) {
      if (!window.p5Instance || isAnimating) return;

      console.log(
         `Master switch triggered — ${isOn ? "Vertical U→D" : "Vertical D→U"}`,
      );
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
            window.p5Instance.triggerAnimation(item.row, item.col, isOn);

            if (index === sequence.length - 1) {
               setTimeout(() => {
                  isAnimating = false;
               }, 500);
            }
         }, index * 150);
      });
   }

   function timerForIdling() {
      const min = 200;
      const max = 450;
      const rand = Math.floor(Math.random() * (max - min + 1) + min);

      setTimeout(() => {
         if (!window.p5Instance) {
            console.log("Idling: p5 not ready");
            timerForIdling();
            return;
         }
         if (isAnimating) {
            console.log("Idling: animation in progress, skipping");
            timerForIdling();
            return;
         }

         const allOff = (() => {
            const grid = window.p5Instance.getEllipseStates?.();
            if (!grid) return false;
            for (let r = 0; r < 4; r++) {
               for (let c = 0; c < 4; c++) {
                  if (grid[r][c]) return false;
               }
            }
            return true;
         })();

         if (!allOff) {
            console.log("Idling: some switches ON, skipping");
            timerForIdling();
            return;
         }

         console.log("🌙 Idle detected → triggering idle animation...");

         const row = Math.floor(Math.random() * 4);
         const col = Math.floor(Math.random() * 4);

         const patternList = [
            "ripple",
            "horizontal_lr",
            "horizontal_rl",
            "vertical_ud",
            "vertical_du",
            "random",
         ];
         const chosenPattern =
            patternList[Math.floor(Math.random() * patternList.length)];

         currentPattern = chosenPattern;
         console.log(`🌙 Idle pattern selected: ${chosenPattern}`);

         if (mqttClient?.connected) {
            mqttClient.publish("pattern/set", chosenPattern);
         }

         window.p5Instance.triggerAnimation(row, col, true);

         timerForIdling();
      }, rand * 1000);
   }

   function publishSwitchCommand(row, col, state) {
      const flippedCol = 3 - col;
      const switchNum = row * 4 + flippedCol + 1;
      if (mqttClient?.connected) {
         mqttClient.publish(`switch/${switchNum}`, state ? "ON" : "OFF");
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

   function publishMasterState(isOn) {
      if (mqttClient?.connected) {
         mqttClient.publish("switch/master", isOn ? "ON" : "OFF");
         console.log(`Published master ${isOn ? "ON" : "OFF"}`);
      }
   }

   async function setupRNBO() {
      const WAContext = window.AudioContext || window.webkitAudioContext;
      audioContext = new WAContext();

      // COMPLETELY SEPARATE AUDIO CHAINS
      const mainGain = audioContext.createGain(); // Only for main synth
      const droneGain = audioContext.createGain(); // Only for drone
      const masterGain = audioContext.createGain(); // Final output

      // INDEPENDENT VOLUME CONTROLS
      mainGain.gain.value = 0.6; // Reduced from 0.7 to prevent clipping
      droneGain.gain.value = 0.12; // Drone volume (independent!)

      // Connect to master output
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

         // CREATE TWO COMPLETELY SEPARATE DEVICES
         mainSynth = await window.RNBO.createDevice({
            context: audioContext,
            patcher: synthPatcher,
         });

         droneDevice = await window.RNBO.createDevice({
            context: audioContext,
            patcher: dronePatcher,
         });

         // CONNECT TO SEPARATE GAIN NODES
         mainSynth.node.connect(mainGain); // Synth → mainGain only
         droneDevice.node.connect(droneGain); // Drone → droneGain only

         // SETUP EACH INDEPENDENTLY
         setupMainSynth();
         setupDrone();
      } catch (err) {
         console.error("RNBO setup failed:", err);
      }
   }

   function setupDrone() {
      if (!droneDevice) return;

      try {
         const droneParams = {
            volume: 1.4,

            droneOn: 1,

            droneFilterType: 0,
            droneFilterCut: 1000,
            droneFilterQ: 0.6,

            harmonics: 0.7, // drastically reduce harshness
            overblow: 0.1,
            fluctuate: 0.03,

            reverbMix: 0.35, // reduce tail buildup
            reverb_decay: 0.5,
            reverb_rotate: 0.1,
            damping: 0.15,
         };

         // Apply ONLY to droneDevice
         Object.entries(droneParams).forEach(([key, value]) => {
            const param = droneDevice.parametersById.get(key);
            if (param) {
               param.value = value;
               console.log(`   Drone: ${key} = ${value}`);
            }
         });

         // Start drone with MIDI notes
         const midiPort = 0;
         const velocity = 60;
         const currentTime = droneDevice.context.currentTime * 1000;

         droneNotes.forEach((note) => {
            const noteOn = [144, note, velocity];
            droneDevice.scheduleEvent(
               new window.RNBO.MIDIEvent(currentTime, midiPort, noteOn),
            );
         });

         droneActive = true;
         console.log("Drone started (background harmony only)");
      } catch (err) {
         console.error("Drone setup error:", err);
      }
   }

   // ═══════════════════════════════════════════════════════════
   // MAIN SYNTH SETUP - COMPLETELY INDEPENDENT FROM DRONE
   // ═══════════════════════════════════════════════════════════
   function setupMainSynth() {
      if (!mainSynth) return;

      // MAIN SYNTH-ONLY PARAMETERS - FIX HARSH LINGERING NOISE
      const params = {
         // Filter - LOWER to remove harsh frequencies
         filterCut: 1000, // Reduced from 1500 for less harshness
         filterQ: 0.15, // Reduced from 0.2 for smoother
         filterType: 1, // Lowpass

         // Reverb - LESS reverb to avoid buildup
         reverbTime: 4,
         reverbMix: 0.4,

         // Tuning
         setTuning: 0,

         // Envelope - CRITICAL: Smooth attack and LONG release to fade cleanly
         "poly/envelope/attack": 50, // Increased from 30 - gentler start
         "poly/envelope/decay": 200, // Increased from 150
         "poly/envelope/sustain": 0.5, // Reduced from 0.7 - cleaner
         "poly/envelope/release": 800, // Increased from 800 - longer fade to silence

         "poly/oscillator/mode": 3,

         // Delay - REDUCE FEEDBACK to prevent noise buildup
         //"poly/delay/left_delay": 300,
         //"poly/delay/fb": 0.15, // Reduced from 0.3 - less feedback
         //"poly/delay/right_delay": 400,
      };

      Object.entries(params).forEach(([key, value]) => {
         const param = mainSynth.parametersById.get(key);
         if (param) {
            param.value = value;
            console.log(`   Synth: ${key} = ${value}`);
         }
      });
   }

   // ═══════════════════════════════════════════════════════════
   // PLAY NOTE - ONLY AFFECTS MAIN SYNTH, NOT DRONE
   // ═══════════════════════════════════════════════════════════
   function playMIDINote(note, duration = 400) {
      // Increased from 250 for cleaner release
      if (!mainSynth || !audioContext) return;

      if (audioContext.state === "suspended") {
         audioContext.resume();
      }

      // Send MIDI to MAIN SYNTH ONLY (not drone)
      const midiChannel = 0;
      const midiPort = 0;
      const velocity = 70; // Reduced from 80 for gentler sound
      const currentTime = mainSynth.context.currentTime * 1000;

      // Note On
      const noteOnMessage = [144 + midiChannel, note, velocity];
      const noteOnEvent = new window.RNBO.MIDIEvent(
         currentTime,
         midiPort,
         noteOnMessage,
      );
      mainSynth.scheduleEvent(noteOnEvent); // ONLY to mainSynth

      const noteOffMessage = [128 + midiChannel, note, 0];
      const noteOffEvent = new window.RNBO.MIDIEvent(
         currentTime + duration,
         midiPort,
         noteOffMessage,
      );
      mainSynth.scheduleEvent(noteOffEvent); // ONLY to mainSynth

      console.log(`🎵 Note ${note} (vel ${velocity}, dur ${duration}ms)`);
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

      const params = {
         filterCut: 1000,
         filterQ: 0.15,
         filterType: 1,
         reverbTime: 8,
         reverbMix: 0.4,
         setTuning: 0,
         "poly/envelope/attack": 50,
         "poly/envelope/decay": 100,
         "poly/envelope/sustain": 0.5,
         "poly/envelope/release": 200,
         "poly/oscillator/mode": Math.floor(Math.random() * 3),
      };

      applySynthParams(params);
   }

   function applySynthParams(params) {
      if (!mainSynth) return;

      Object.entries(params).forEach(([key, value]) => {
         const p = mainSynth.parametersById.get(key);
         if (p) p.value = value;
      });
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
                  console.log(
                     "All switches OFF - regenerating notes and shuffling pattern...",
                  );

                  const newNotes = await generateMelody(16);
                  let idx = 0;
                  for (let i = 0; i < rows; i++) {
                     for (let j = 0; j < cols; j++) {
                        midiNotes[i][j] = newNotes[idx++] || 60;
                     }
                  }
                  console.log("🎹 Notes regenerated:", midiNotes);

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

               for (let j = 0; j < rows; j++) {
                  for (let i = 0; i < cols; i++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               const clickedIndex = clickedRow * cols + clickedCol;

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

               for (let j = rows - 1; j >= 0; j--) {
                  for (let i = cols - 1; i >= 0; i--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               const totalSwitches = rows * cols;
               const clickedIndex =
                  totalSwitches - 1 - (clickedRow * cols + clickedCol);

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

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               const clickedIndex = clickedCol * rows + clickedRow;

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

               for (let i = cols - 1; i >= 0; i--) {
                  for (let j = rows - 1; j >= 0; j--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               const totalSwitches = rows * cols;
               const clickedIndex =
                  totalSwitches - 1 - (clickedCol * rows + clickedRow);

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
                  }, index * 500);
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
                  }, index * 500);
               });

               return totalDuration + 500;
            }

            function sendDroneNote(note, duration = 800) {
               if (!droneDevice) return;

               const midiChannel = 1; // keep separate from main synth
               const midiPort = 0;
               const velocity = 70; // softer than main synth
               const now = droneDevice.context.currentTime * 1000;

               // Note-on
               droneDevice.scheduleEvent(
                  new window.RNBO.MIDIEvent(now, midiPort, [
                     144 + midiChannel,
                     note,
                     velocity,
                  ]),
               );

               // Note-off
               droneDevice.scheduleEvent(
                  new window.RNBO.MIDIEvent(now + duration, midiPort, [
                     128 + midiChannel,
                     note,
                     0,
                  ]),
               );

               console.log(`🌫 Drone note: ${note}`);
            }
            function updateSwitch(row, col, state, midiNote) {
               ellipseStates[row][col] = state;
               if (state) {
                  playMIDINote(midiNote); // main synth
                  sendDroneNote(midiNote); // <-- NEW, drone reacts too
               }

               publishSwitchCommand(row, col, state);
               publishState(row, col, state, midiNote);

               // Drone is independent - no interaction here
               // Drone keeps playing in background regardless of switches
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
                        playMIDINote(midiNotes[row][col], 400); // Clean release
                     }
                  }
               },
               getEllipseStates: () => {
                  return ellipseStates;
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
                  case " ":
                     // Toggle drone on/off with spacebar
                     if (droneDevice) {
                        const droneOnParam =
                           droneDevice.parametersById.get("droneOn");
                        if (droneOnParam) {
                           droneOnParam.value = droneOnParam.value ? 0 : 1;
                           console.log(
                              `Drone: ${droneOnParam.value ? "ON" : "OFF"}`,
                           );
                        }
                     }
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
</style>
