<script>
   import { onMount, onDestroy } from "svelte";
   import mqtt from "mqtt";

   let mainSynth = null;
   let droneDevice = null;
   let audioContext = null;
   let magentaModel = null;
   let mqttClient = null;
   let currentPattern = [
      "ripple",
      "horizontal_lr",
      "horizontal_rl",
      "vertical_ud",
      "vertical_du",
      "random",
   ][Math.floor(Math.random() * 6)];
   let isAnimating = false;
   let animationTimeout = null;

   let droneNotes = [48, 52, 55, 60, 64, 67];
   // let droneNotes = [
   //    36, 40, 43, 48, 52, 55, 59, 60, 64, 67, 71, 74, 76, 79, 83,
   // ];
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
         resetIdleTimer(); // Add this line
      }
   }

   function handleMasterSwitch(isOn) {
      if (!window.p5Instance || isAnimating) return;
      resetIdleTimer();
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
         }, index * animSpeed);
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

      //  AUDIO KEEPALIVE SYSTEM - PREVENTS 3-SECOND CUTOUT
      audioContext.addEventListener("statechange", () => {
         console.log(
            ` AudioContext: ${audioContext.state} @ ${audioContext.currentTime.toFixed(2)}s`,
         );
         if (audioContext.state === "suspended") {
            console.warn("⚠️ Audio suspended! Resuming...");
            audioContext.resume();
         }
      });

      // Check every second and force resume if needed
      setInterval(() => {
         if (audioContext?.state === "suspended") {
            console.log(" Keepalive: resuming audio");
            audioContext.resume();
         }
      }, 1000);

      // Silent audio stream to keep context alive
      const silenceNode = audioContext.createConstantSource();
      const silenceGain = audioContext.createGain();
      silenceGain.gain.value = 0.0001; // Nearly silent
      silenceNode.connect(silenceGain);
      silenceGain.connect(audioContext.destination);
      silenceNode.start();
      console.log(" Silence keepalive started");

      const mainGain = audioContext.createGain();
      const droneGain = audioContext.createGain();
      const masterGain = audioContext.createGain();

      mainGain.gain.value = 0.9;
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
         setTuning: 3,
         attack: 10,
         decay: 150,
         sustain: 0.6,
         release: 800,
         oscMode: 1,
         leftDelay: 250,
         delayFb: 0.5,
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

   let droneParams = {};

   function setupDrone() {
      if (!droneDevice) {
         console.error("droneDevice is null!");
         return;
      }

      console.log("\nSETTING UP DRONE...");

      droneParams = {
         volume: droneDevice.parametersById.get("volume"),
         droneOn: droneDevice.parametersById.get("droneOn"),
         droneFilterType: droneDevice.parametersById.get("droneFilterType"),
         droneFilterCut: droneDevice.parametersById.get("droneFilterCut"),
         droneFilterQ: droneDevice.parametersById.get("droneFilterQ"),
         harmonics: droneDevice.parametersById.get("harmonics"),
         overblow: droneDevice.parametersById.get("overblow"),
         fluctuate: droneDevice.parametersById.get("fluctuate"),
         reverbMix: droneDevice.parametersById.get("reverbMix"),
         reverb_decay: droneDevice.parametersById.get("reverb_decay"),
         reverb_rotate: droneDevice.parametersById.get("reverb_rotate"),
         damping: droneDevice.parametersById.get("damping"),
      };

      const initialValues = {
         volume: 1,
         droneOn: 1,
         droneFilterType: 0,
         droneFilterCut: 800,
         droneFilterQ: 0.3,
         harmonics: 0.6,
         overblow: 0,
         fluctuate: 0.001,
         reverb_decay: 8,
         reverb_rotate: 0.3,
         damping: 0.2,
      };

      Object.entries(initialValues).forEach(([key, value]) => {
         const param = droneDevice.parametersById.get(key);
         if (param) {
            param.value = value;
            console.log(`   ${key} = ${value}`);
         } else {
            console.warn(`   NOT FOUND: ${key}`);
         }
      });

      const midiPort = 0;
      const velocity = 90;
      const currentTime = droneDevice.context.currentTime * 1000;

      // console.log("\nStarting drone: C major chord (C3, E3, G3, C4, E4, G4)");
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
            synthParams.attack.value = 50;
            synthParams.delayFb.value = 1.3;
            synthParams.reverbTime.value = 3;
            synthParams.reverb_rotate.value = 0.6;
            // droneParams.harmonics.value =
            //    Math.floor(Math.random() * (2 + 1)) + 1;
            // droneParams.droneFilterQ.value = 1;
            //droneParams.harmonics.value = 3;
            //droneParams.volume.value = 0.5;
            break;
         case 2:
            synthParams.filterCut.value = 1300;
            synthParams.attack.value = 0;
            synthParams.release.value = 300;
            synthParams.reverbMix.value = 0.2;
            synthParams.filterCut.value = 500;
            synthParams.reverb_rotate.value = 0.2;

            // droneParams.volume.value = 0.3;

            break;
         case 3:
            synthParams.filterCut.value = 1000;
            synthParams.filterCut.value = 500;
            synthParams.attack.value = 0;
            synthParams.release.value = 300;
            synthParams.reverb_rotate.value = 0.2;
            // droneParams.harmonics.value =
            //    Math.floor(Math.random() * (2 + 1)) + 1;
            // droneParams.overblow.value =
            //    Math.floor(Math.random() * (2 + 1)) + 1;
            // droneParams.damping.value = 0.5;
            // synthParams.reverb_rotate.value = 0.2;
            //droneParams.filterCut.value = 1200;
            //droneParams.volume.value = 0.3;
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
                  animSpeed = Math.floor(Math.random() * (650 - 250 + 1)) + 250;

                  // Pattern randomization - exclude current pattern
                  const patternIds = [
                     "ripple",
                     "horizontal_lr",
                     "horizontal_rl",
                     "vertical_ud",
                     "vertical_du",
                     "random",
                  ];
                  const availablePatterns = patternIds.filter(
                     (p) => p !== currentPattern,
                  );

                  const randomPattern =
                     availablePatterns[
                        Math.floor(Math.random() * availablePatterns.length)
                     ];
                  currentPattern = randomPattern;

                  if (mqttClient?.connected) {
                     mqttClient.publish("pattern/set", randomPattern);
                  }
                  console.log(`Pattern selected: ${randomPattern}`); // Better logging
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

            let animSpeed = 300;
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
                  }, index * animSpeed);
               });

               return ellipsesWithDistance.length * 300 + 500;
            }

            function horizontalLRPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Start with clicked row, go right from clicked column
               for (let offsetCol = 0; offsetCol < cols; offsetCol++) {
                  const col = (clickedCol + offsetCol) % cols;
                  sequence.push({
                     row: clickedRow,
                     col: col,
                     midiNote: midiNotes[clickedRow][col],
                  });
               }

               // Then do other rows, left to right
               for (let j = 0; j < rows; j++) {
                  if (j === clickedRow) continue; // Skip clicked row (already done)

                  for (let i = 0; i < cols; i++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);
                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * animSpeed);
               });

               return sequence.length * 150 + 500;
            }

            function horizontalRLPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Start with clicked row, go left from clicked column
               for (let offsetCol = 0; offsetCol < cols; offsetCol++) {
                  const col = (clickedCol - offsetCol + cols) % cols;
                  sequence.push({
                     row: clickedRow,
                     col: col,
                     midiNote: midiNotes[clickedRow][col],
                  });
               }

               // Then do other rows, right to left
               for (let j = 0; j < rows; j++) {
                  if (j === clickedRow) continue;

                  for (let i = cols - 1; i >= 0; i--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);
                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * animSpeed);
               });

               return sequence.length * 150 + 500;
            }

            function verticalUDPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Start with clicked column, go down from clicked row
               for (let offsetRow = 0; offsetRow < rows; offsetRow++) {
                  const row = (clickedRow + offsetRow) % rows;
                  sequence.push({
                     row: row,
                     col: clickedCol,
                     midiNote: midiNotes[row][clickedCol],
                  });
               }

               // Then do other columns, top to bottom
               for (let i = 0; i < cols; i++) {
                  if (i === clickedCol) continue;

                  for (let j = 0; j < rows; j++) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);
                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * animSpeed);
               });

               return sequence.length * 150 + 500;
            }

            function verticalDUPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Start with clicked column, go up from clicked row
               for (let offsetRow = 0; offsetRow < rows; offsetRow++) {
                  const row = (clickedRow - offsetRow + rows) % rows;
                  sequence.push({
                     row: row,
                     col: clickedCol,
                     midiNote: midiNotes[row][clickedCol],
                  });
               }

               // Then do other columns, bottom to top
               for (let i = 0; i < cols; i++) {
                  if (i === clickedCol) continue;

                  for (let j = rows - 1; j >= 0; j--) {
                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);
                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * animSpeed);
               });

               return sequence.length * 150 + 500;
            }

            function randomPattern(clickedRow, clickedCol, newState) {
               let sequence = [];

               // Add clicked ellipse FIRST
               sequence.push({
                  row: clickedRow,
                  col: clickedCol,
                  midiNote: midiNotes[clickedRow][clickedCol],
               });

               // Create array of all OTHER positions
               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     // Skip the clicked one since we already added it
                     if (j === clickedRow && i === clickedCol) continue;

                     sequence.push({
                        row: j,
                        col: i,
                        midiNote: midiNotes[j][i],
                     });
                  }
               }

               // Shuffle only the remaining elements (not the first one)
               for (let i = sequence.length - 1; i > 1; i--) {
                  const j = Math.floor(Math.random() * (i - 1)) + 1; // Start from index 1
                  [sequence[i], sequence[j]] = [sequence[j], sequence[i]];
               }

               sequence.forEach((item, index) => {
                  setTimeout(() => {
                     updateSwitch(item.row, item.col, newState, item.midiNote);
                     if (index === sequence.length - 1) {
                        setTimeout(() => {
                           isAnimating = false;
                           regenerateNotes();
                        }, 500);
                     }
                  }, index * animSpeed);
               });

               return sequence.length * 150 + 500;
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

               let duration;
               switch (currentPattern) {
                  case "horizontal_lr":
                     duration = horizontalLRPattern(row, col, newState);
                     break;
                  case "horizontal_rl":
                     duration = horizontalRLPattern(row, col, newState);
                     break;
                  case "vertical_ud":
                     duration = verticalUDPattern(row, col, newState);
                     break;
                  case "vertical_du":
                     duration = verticalDUPattern(row, col, newState);
                     break;
                  case "random":
                     duration = randomPattern(row, col, newState);
                     break;
                  case "ripple":
                  default:
                     duration = ripplePattern(row, col, newState);
                     break;
               }

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
            const fontPath = "/fonts/Cardinal.ttf";

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

            let p5Font;
            p.preload = () => {
               p5Font = p.loadFont(fontPath);
            };

            p.setup = () => {
               p.createCanvas(canvasSize, canvasSize);
               p.background(255);
               p.textAlign(p.CENTER, p.CENTER);
               p.textFont(p5Font);
               p.noSmooth();
            };

            p.draw = () => {
               //p.background(255);

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);

                     // p.rect(
                     //    xSpacing - x,
                     //    ySpacing - y,
                     //    innerWidth,
                     //    innerHeight,
                     // );
                     p.fill(ellipseStates[j][i] ? 0 : 255);
                     p.stroke(0);
                     p.strokeWeight(1.4);
                     p.ellipse(x, y, ellipseSize, ellipseSize);
                     p.fill(ellipseStates[j][i] ? 255 : 0);
                     p.textSize(50);
                     p.text(ellipseStates[j][i] ? "Off" : "On", x, y - 5);
                     p.textSize(12);
                     //p.text(`${midiNotes[j][i]}`, x, y + 20);
                  }
               }
            };

            p.mousePressed = () => {
               if (isAnimating) return;

               if (audioContext?.state === "suspended") {
                  audioContext.resume().then(() => {
                     console.log("Audio context resumed");
                  });
               }
               resetIdleTimer(); // Add this line

               for (let i = 0; i < cols; i++) {
                  for (let j = 0; j < rows; j++) {
                     const x = xSpacing * (i + 1);
                     const y = ySpacing * (j + 1);
                     const distance = p.dist(p.mouseX, p.mouseY, x, y);
                     if (distance < ellipseSize / 2) {
                        triggerAnimation(j, i, !ellipseStates[j][i]);
                        p.background(ellipseStates[j][i] ? 0 : 255);
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

   let lastInteractionTime = Date.now();
   let idleCheckInterval = null;
   const IDLE_TIMEOUT = Math.floor(Math.random() * 5 + 1) * 60 * 1000;
   function startIdleChecker() {
      // Clear any existing interval
      if (idleCheckInterval) {
         clearInterval(idleCheckInterval);
      }

      // Check every 30 seconds if we've been idle
      idleCheckInterval = setInterval(() => {
         const timeSinceLastInteraction = Date.now() - lastInteractionTime;

         if (timeSinceLastInteraction >= IDLE_TIMEOUT && !isAnimating) {
            console.log("IDLE: Triggering random animation");
            triggerRandomIdleAnimation();
         }
      }, 30000); // Check every 30 seconds
   }

   function triggerRandomIdleAnimation() {
      if (!window.p5Instance || isAnimating) return;

      const randomRow = Math.floor(Math.random() * 4);
      const randomCol = Math.floor(Math.random() * 4);

      const states = window.p5Instance.getEllipseStates();
      const currentState = states[randomRow][randomCol];

      window.p5Instance.triggerAnimation(randomRow, randomCol, !currentState);

      lastInteractionTime = Date.now();
   }

   function resetIdleTimer() {
      lastInteractionTime = Date.now();
   }

   onMount(() => {
      console.log("\nSTARTING...\n");
      setupRNBO();
      setupMQTT();
      startIdleChecker();

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
      if (idleCheckInterval) {
         clearInterval(idleCheckInterval); // Add this
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
</style>
