document.addEventListener('DOMContentLoaded', async () => {
    // Odwołania do elementów
    const invalidLinkContainer = document.getElementById('invalidLinkContainer');
    const bookingContainer = document.getElementById('bookingContainer');
    const reservationForm = document.getElementById('reservationForm');
    const reserveButton = document.getElementById('reserveButton');
    const reservationStatus = document.getElementById('reservationStatus');
    const calendarContainer = document.getElementById('calendar-container');
    const firstNameInput = document.getElementById('firstName');
    const lastNameInput = document.getElementById('lastName');
    const subjectSelect = document.getElementById('subject');
    const schoolTypeSelect = document.getElementById('schoolType');
    const classGroup = document.getElementById('classGroup');
    const schoolClassSelect = document.getElementById('schoolClass');
    const levelGroup = document.getElementById('levelGroup');
    const schoolLevelSelect = document.getElementById('schoolLevel');
    const chooseTutorCheckbox = document.getElementById('chooseTutorCheckbox');
    const tutorGroup = document.getElementById('tutorGroup');
    const tutorSelect = document.getElementById('tutorSelect');
    
    // Lista pól do podstawowej walidacji (bez dynamicznych)
    const baseFormFields = [subjectSelect, schoolTypeSelect];
    let clientID = null;

    // --- GŁÓWNA LOGIKA INICJALIZACJI APLIKACJI ---
    async function initializeApp() {
        const params = new URLSearchParams(window.location.search);
        clientID = params.get('clientID');

        if (!clientID) {
            displayInvalidLinkError();
            return;
        }

        try {
            const clientData = await verifyClient(clientID);
            prepareBookingForm(clientData);
            // Inicjalizuj resztę aplikacji po pomyślnej weryfikacji
            initializeEventListeners();
            updateSchoolDependentFields();
            handleTutorSelection();
            fetchAvailableSlots(currentWeekStart);
        } catch (error) {
            displayInvalidLinkError(error.message);
        }
    }

    function displayInvalidLinkError(message = "Nieprawidłowy link. Skontaktuj się z obsługą klienta, aby otrzymać swój osobisty link do rezerwacji.") {
        if(bookingContainer) bookingContainer.style.display = 'none';
        if(invalidLinkContainer) {
            invalidLinkContainer.style.display = 'block';
            const p = invalidLinkContainer.querySelector('p');
            if (p) p.textContent = message;
        }
    }

    async function verifyClient(id) {
        const response = await fetch(`https://zakrecone-korepetycje-api-467795448922.europe-central2.run.app/api/verify-client?clientID=${id}`);
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.message || "Nie udało się zweryfikować klienta.");
        }
        return await response.json();
    }

    function prepareBookingForm(clientData) {
        firstNameInput.value = clientData.firstName;
        lastNameInput.value = clientData.lastName;
        bookingContainer.style.display = 'flex';
    }

    // --- POZOSTAŁE FUNKCJE ---
    let selectedSlotId = null;
    let selectedDate = null;
    let selectedTime = null;
    let currentWeekStart = getMonday(new Date());
    let availableSlotsData = {};
    const monthNames = ["Styczeń", "Luty", "Marzec", "Kwiecień", "Maj", "Czerwiec", "Lipiec", "Sierpień", "Wrzesień", "Październik", "Listopad", "Grudzień"];
    const dayNamesFull = ["Niedziela", "Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota"];
    const workingHoursStart = 8;
    const workingHoursEnd = 22;
    const schoolClasses = {
        'szkola_podstawowa': ['1', '2', '3', '4', '5', '6', '7', '8'],
        'liceum': ['1', '2', '3', '4'],
        'technikum': ['1', '2', '3', '4', '5']
    };

    function checkFormValidity() {
        const isBaseFormValid = baseFormFields.every(field => field.checkValidity());
        let isClassValid = classGroup.style.display === 'none' || schoolClassSelect.checkValidity();
        let isLevelValid = levelGroup.style.display === 'none' || schoolLevelSelect.checkValidity();
        let isTutorValid = tutorGroup.style.display === 'none' || (tutorSelect.value !== "");
        reserveButton.disabled = !(isBaseFormValid && isClassValid && isLevelValid && isTutorValid && selectedSlotId !== null);
    }
    function showStatus(message, type) {
        reservationStatus.textContent = message;
        reservationStatus.className = `reservation-status ${type}`;
        reservationStatus.style.display = 'block';
        setTimeout(() => {
            reservationStatus.style.display = 'none';
        }, 5000);
    }
    function getFormattedDate(date) {
        const yyyy = date.getFullYear();
        const mm = String(date.getMonth() + 1).padStart(2, '0');
        const dd = String(date.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
    }
    function getMonday(d) {
        d = new Date(d);
        const day = d.getDay();
        const diff = d.getDate() - day + (day === 0 ? -6 : 1);
        return new Date(d.setDate(diff));
    }
    function updateSchoolDependentFields() {
        const selectedSchoolType = schoolTypeSelect.value;
        schoolClassSelect.innerHTML = '<option value="">Wybierz klasę</option>';
        if (selectedSchoolType in schoolClasses) {
            classGroup.style.display = 'block';
            schoolClasses[selectedSchoolType].forEach(cls => {
                const option = document.createElement('option');
                option.value = cls;
                option.textContent = cls;
                schoolClassSelect.appendChild(option);
            });
            schoolClassSelect.required = true;
        } else {
            classGroup.style.display = 'none';
            schoolClassSelect.required = false;
        }
        if (selectedSchoolType === 'liceum' || selectedSchoolType === 'technikum') {
            levelGroup.style.display = 'block';
            schoolLevelSelect.required = true;
        } else {
            levelGroup.style.display = 'none';
            schoolLevelSelect.required = false;
            schoolLevelSelect.value = '';
        }
    }
    function handleTutorSelection() {
        if (chooseTutorCheckbox.checked) {
            tutorGroup.style.display = 'block';
            tutorSelect.required = true;
        } else {
            tutorGroup.style.display = 'none';
            tutorSelect.required = false;
            tutorSelect.value = '';
        }
        generateTimeSlotCalendar(currentWeekStart);
        checkFormValidity();
    }
    function selectSlot(slotId, element, date, time) {
        const prevSelected = document.querySelector('.time-block.selected');
        if (prevSelected) prevSelected.classList.remove('selected');
        element.classList.add('selected');
        selectedSlotId = slotId;
        selectedDate = date;
        selectedTime = time;
        checkFormValidity();
    }
    function changeWeek(days) {
        currentWeekStart.setDate(currentWeekStart.getDate() + days);
        selectedSlotId = null;
        selectedDate = null;
        selectedTime = null;
        checkFormValidity();
        fetchAvailableSlots(currentWeekStart);
    }
    function updateTutorList(newTutors) {
        const currentTutorsInSelect = Array.from(tutorSelect.options).map(o => o.value).filter(v => v);
        if (JSON.stringify(newTutors.sort()) === JSON.stringify(currentTutorsInSelect.sort())) return;
        tutorSelect.innerHTML = '<option value="">Wybierz korepetytora</option>';
        newTutors.forEach(tutor => {
            const option = document.createElement('option');
            option.value = tutor;
            option.textContent = tutor;
            tutorSelect.appendChild(option);
        });
    }
    async function generateTimeSlotCalendar(startDate) {
        calendarContainer.innerHTML = '';
        calendarContainer.className = 'time-slot-calendar';
        const daysInWeek = Array.from({length: 7}, (_, i) => { const d = new Date(startDate); d.setDate(d.getDate() + i); return d; });
        const calendarNavigation = document.createElement('div');
        calendarNavigation.className = 'calendar-navigation';
        const firstDayFormatted = `${dayNamesFull[daysInWeek[0].getDay()].substring(0,3)}. ${daysInWeek[0].getDate()} ${monthNames[daysInWeek[0].getMonth()].substring(0,3)}.`;
        const lastDayFormatted = `${dayNamesFull[daysInWeek[6].getDay()].substring(0,3)}. ${daysInWeek[6].getDate()} ${monthNames[daysInWeek[6].getMonth()].substring(0,3)}.`;
        calendarNavigation.innerHTML = `<button id="prevWeek">Poprzedni tydzień</button><h3>${firstDayFormatted} - ${lastDayFormatted}</h3><button id="nextWeek">Następny tydzień</button>`;
        calendarContainer.appendChild(calendarNavigation);
        const table = document.createElement('table');
        table.className = 'calendar-grid-table';
        const thead = table.createTHead();
        let headerRowHtml = '<tr><th class="time-label">Godzina</th>';
        daysInWeek.forEach(day => { headerRowHtml += `<th>${dayNamesFull[day.getDay()]}<br>${String(day.getDate()).padStart(2, '0')} ${monthNames[day.getMonth()].substring(0, 3)}</th>`; });
        headerRowHtml += '</tr>';
        thead.innerHTML = headerRowHtml;
        const tbody = table.createTBody();
        const selectedTutor = tutorSelect.value;
        for (let h = workingHoursStart; h < workingHoursEnd; h++) {
            const row = tbody.insertRow();
            row.insertCell().outerHTML = `<td class="time-label">${String(h).padStart(2, '0')}:00</td>`;
            daysInWeek.forEach(day => {
                const cell = row.insertCell();
                const formattedDate = getFormattedDate(day);
                const blockStartTime = `${String(h).padStart(2, '0')}:00`;
                const blockId = `block_${formattedDate}_${blockStartTime.replace(':', '')}`;
                const currentDaySlots = availableSlotsData[formattedDate] || [];
                let matchingSlot = null;
                if (chooseTutorCheckbox.checked && selectedTutor) {
                    matchingSlot = currentDaySlots.find(slot => slot.time === blockStartTime && slot.tutor === selectedTutor);
                } else {
                    matchingSlot = currentDaySlots.find(slot => slot.time === blockStartTime);
                }
                const block = document.createElement('div');
                block.className = 'time-block';
                block.dataset.slotId = blockId;
                block.dataset.date = formattedDate;
                block.dataset.time = blockStartTime;
                const currentMoment = new Date();
                const blockDateTime = new Date(`${formattedDate}T${blockStartTime}:00`);
                let isClickable = false;
                let blockContent = '';
                if (blockDateTime < currentMoment) {
                    block.classList.add('past');
                    blockContent = matchingSlot ? blockStartTime : '';
                } else if (matchingSlot) {
                    blockContent = blockStartTime;
                    isClickable = true;
                } else {
                    block.classList.add('disabled');
                }
                if (selectedSlotId === blockId) {
                    block.classList.add('selected');
                }
                block.textContent = blockContent;
                if(isClickable) {
                    block.addEventListener('click', () => selectSlot(blockId, block, formattedDate, blockStartTime));
                } else {
                    block.setAttribute('disabled', '');
                }
                cell.appendChild(block);
            });
        }
        calendarContainer.appendChild(table);
        document.getElementById('prevWeek').addEventListener('click', () => changeWeek(-7));
        document.getElementById('nextWeek').addEventListener('click', () => changeWeek(7));
    }
    async function fetchAvailableSlots(startDate) {
        console.log(`Pobieram dostępne sloty z Airtable dla tygodnia od ${getFormattedDate(startDate)}...`);
        try {
            const response = await fetch(`https://zakrecone-korepetycje-api-467795448922.europe-central2.run.app/api/get-schedule?startDate=${getFormattedDate(startDate)}`);
            if (!response.ok) { throw new Error('Błąd pobierania danych z serwera'); }
            const scheduleFromApi = await response.json();
            const processedData = {};
            const uniqueTutors = new Set();
            scheduleFromApi.forEach(slot => {
                const { date, time, tutor } = slot;
                if (!processedData[date]) { processedData[date] = []; }
                processedData[date].push({ id: `block_${date}_${time.replace(':', '')}_${tutor.replace(' ', '_')}`, time: time, tutor: tutor, duration: 60 });
                uniqueTutors.add(tutor);
            });
            availableSlotsData = processedData;
            updateTutorList(Array.from(uniqueTutors));
            generateTimeSlotCalendar(startDate);
        } catch (error) {
            console.error('Nie udało się pobrać grafiku:', error);
            showStatus('Błąd ładowania grafiku. Spróbuj ponownie później.', 'error');
        }
    }
    
    function initializeEventListeners() {
        reservationForm.addEventListener('change', (event) => {
            const targetId = event.target.id;
            if (targetId === 'schoolType') {
                updateSchoolDependentFields();
            } else if (targetId === 'chooseTutorCheckbox' || targetId === 'tutorSelect') {
                handleTutorSelection();
            }
            checkFormValidity();
        });
        
        reservationForm.addEventListener('input', checkFormValidity);

        reserveButton.addEventListener('click', async (e) => {
            e.preventDefault();
            if (!reservationForm.checkValidity() || !selectedSlotId) {
                showStatus('Proszę wypełnić wszystkie wymagane pola i wybrać termin.', 'error');
                return;
            }
            const formData = {
                clientID: clientID, // Dodaj ClientID do wysyłanych danych
                firstName: firstNameInput.value, lastName: lastNameInput.value, subject: subjectSelect.value,
                schoolType: schoolTypeSelect.value,
                schoolLevel: levelGroup.style.display === 'block' ? schoolLevelSelect.value : null,
                schoolClass: classGroup.style.display === 'block' ? schoolClassSelect.value : null,
                tutor: chooseTutorCheckbox.checked ? tutorSelect.value : "Dowolny dostępny",
                selectedDate: selectedDate, selectedTime: selectedTime
            };
            reserveButton.disabled = true;
            reserveButton.textContent = 'Rezerwuję...';
            showStatus('Trwa rezerwacja...', 'info');
            try {
                const response = await fetch('https://zakrecone-korepetycje-api-467795448922.europe-central2.run.app/api/create-reservation', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(formData),
                });
                if (response.ok) {
                    const result = await response.json();
                    window.location.href = `confirmation.html?date=${formData.selectedDate}&time=${formData.selectedTime}&teamsUrl=${encodeURIComponent(result.teamsUrl)}&token=${result.managementToken}&clientID=${result.clientID}`;
                } else {
                    const errorData = await response.json();
                    showStatus(`Błąd rezerwacji: ${errorData.message || 'Nie udało się utworzyć spotkania.'}`, 'error');
                }
            } catch (error) {
                console.error('Błąd rezerwacji:', error);
                showStatus('Wystąpił błąd podczas rezerwacji terminu.', 'error');
            } finally {
                reserveButton.disabled = false;
                reserveButton.textContent = 'Zarezerwuj termin';
                checkFormValidity();
            }
        });
    }

    // --- Start aplikacji ---
    initializeApp();
});