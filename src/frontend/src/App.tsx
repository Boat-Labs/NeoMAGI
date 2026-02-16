import { Button } from "@/components/ui/button"

function App() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background text-foreground">
      <h1 className="text-2xl font-bold mb-4">NeoMAGI WebChat</h1>
      <p className="text-muted-foreground mb-4">Frontend scaffolding complete.</p>
      <Button onClick={() => console.log("Button works")}>
        Test Button
      </Button>
    </div>
  )
}

export default App
